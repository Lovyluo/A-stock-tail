# Phase 3.2b-1 Offline Historical Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline, deterministic preparation command that converts `sample` or `local-raw` historical inputs into auditable `daily_proxy` processed datasets accepted by the existing backtest route.

**Architecture:** Add a preparation service with explicit request/result/error models, source adapters limited to committed sample input and local raw CSV files, and writers for processed CSV/YAML plus timestamped audit reports. Keep `LocalCsvHistoricalDataProvider` and the backtest event engine as downstream consumers only; preparation never calls live adapters and backtests never fetch data.

**Tech Stack:** Python standard library (`argparse`, `csv`, `dataclasses`, `datetime`, `pathlib`, `statistics`), existing optional PyYAML parsing path, existing `overnight_quant` daily-proxy provider/engine, `pytest`.

---

## Locked Scope And One Clarification

Phase 3.2b-1 supports only:

```text
--source sample
--source local-raw
```

It returns `SOURCE_NOT_IMPLEMENTED` for `--source a-stock-data` and contains
no HTTP, live-adapter, broker, GUI, model-training, or full-market collection
path.

The approved design requires explicit codes for **every** source. The user's
abbreviated sample invocation must therefore be implemented and verified in
its consistent form:

```text
python overnight_quant/scripts/prepare_backtest_data.py \
  --source sample \
  --codes 300201 \
  --start 2025-01-01 \
  --end 2025-01-31 \
  --out-dir overnight_quant/backtest_data/processed \
  --overwrite
```

Without `--codes` or `--codes-file`, both `sample` and `local-raw` return
`CODES_REQUIRED`.

## File Map

| Path | Responsibility |
| --- | --- |
| `overnight_quant/backtest/data_preparation.py` | Request/result/error models, validation, chronological proxy derivations, processed writers, manifest/audit generation |
| `overnight_quant/backtest/preparation_sources.py` | Offline source protocol, deterministic sample source, local raw CSV source |
| `overnight_quant/scripts/prepare_backtest_data.py` | CLI argument parsing, config path resolution, stable console result codes |
| `overnight_quant/config.yaml` | Default raw, processed, manifest, and preparation-sample paths |
| `overnight_quant/strategy/yang_yongxing_overnight.py` | Mirror new config defaults when YAML is unavailable |
| `overnight_quant/examples/historical_prepare_raw/` | Small committed deterministic source input for `--source sample` only |
| `overnight_quant/backtest_data/README.md` | Document raw input schema, ignored output locations, and preparation commands |
| `overnight_quant/README.md` | Document preparation-to-daily-proxy research workflow and restrictions |
| `overnight_quant/RELEASE_NOTES.md` | Record Phase 3.2b-1 deterministic preparation capability |
| `overnight_quant/tests/test_phase32b_preparation_sources.py` | Focused source-loader tests that can turn green before orchestration exists |
| `overnight_quant/tests/test_phase32b_preparation.py` | Unit/integration contract for preparation, audit files, isolation, and downstream daily-proxy loadability |

No generated processed dataset or preparation audit run artifact is committed.

## Prepared Schema And Derivations

The service writes all five provider input files. The columns are fixed so
empty fields remain auditable rather than disappearing between sources.

### `daily_bars.csv`

```python
DAILY_BAR_FIELDS = [
    "trade_date", "code", "name", "open", "high", "low", "close",
    "volume", "amount", "turnover_pct", "change_pct", "float_mcap_yi",
    "limit_up", "limit_down", "is_st", "is_suspended", "list_date",
    "is_bj_stock", "ma5", "ma10", "ma20", "is_limit_down",
]
```

`sample` provides known price-limit and safety values in its deterministic
fixture. `local-raw` copies safety values only when present in raw rows;
unknown `limit_up`, `limit_down`, `is_st`, `is_suspended`, or `list_date`
remain blank so Phase 3.2a rejects uncertain buys.

The following day-bar calculations use only rows for the same code dated on or
before the current row:

```python
def change_pct(current_close: float, previous_close: float | None) -> float | None:
    return None if previous_close in (None, 0) else round((current_close / previous_close - 1) * 100, 4)

def rolling_ma(prior_and_current_closes: list[float], window: int) -> float:
    sample = prior_and_current_closes[-window:]
    return round(sum(sample) / len(sample), 4)
```

`ma5`, `ma10`, and `ma20` are included because the existing trend scorer reads
them. Their manifest source is `daily_close_rolling_indicator`; they are not
claimed as true 14:50 values.

### `selection_snapshots.csv`

```python
SELECTION_FIELDS = [
    "trade_date", "code", "vol_ratio", "range_position",
    "tail_pullback_pct", "theme_tags", "theme_rank",
    "same_theme_strong_count", "main_net", "big_order_net",
    "source_quality",
]
```

Safe daily proxies are calculated as:

```python
def range_position(close: float, high: float, low: float) -> float | None:
    return None if high <= low else round((close - low) / (high - low), 4)

def volume_ratio_proxy(volume: float, previous_volumes: list[float]) -> float | None:
    sample = previous_volumes[-5:]
    if not sample or sum(sample) <= 0:
        return None
    return round(volume / (sum(sample) / len(sample)), 4)
```

The manifest labels them `daily_range_position_proxy` and
`volume_ratio_proxy`. With no historical intraday, theme, or fund-flow source,
these columns are deliberately blank:

```text
tail_pullback_pct
theme_tags
theme_rank
same_theme_strong_count
main_net
big_order_net
```

The manifest declares:

```text
tail_pullback_pct
theme_tags
theme_rank
main_net
big_order_net
```

as unavailable. `same_theme_strong_count` stays blank as a dependent theme
field and is listed alongside theme unavailability in the prepare report.

### `market_snapshots.csv` And `benchmark_bars.csv`

The committed sample source contains benchmark bars. `local-raw` reads an
optional raw `benchmark_bars.csv`. If benchmark bars exist, the preparer emits
a benchmark-direction market proxy:

```python
direction = round((close / open - 1) * 100, 4) if open else None
market_gate = "PASS" if direction is not None and direction > 0 else "FAIL"
market_reason = (
    "benchmark_direction_proxy"
    if market_gate == "PASS"
    else "benchmark_direction_proxy_non_positive"
)
market_proxy_used = True
```

If no benchmark rows exist for a date, no artificial PASS row is generated;
downstream daily-proxy retains `market_data_unavailable`.

## Task 1: Lock The Preparation Contract With Failing Tests

**Files:**
- Create: `overnight_quant/tests/test_phase32b_preparation.py`

- [ ] **Step 1: Add shared temporary input and config helpers**

Create helpers that keep all generated paths beneath `tmp_path`:

```python
import csv
from pathlib import Path

from overnight_quant.scripts.run_backtest import run_backtest
from overnight_quant.strategy.yang_yongxing_overnight import load_config


def _config(tmp_path: Path) -> dict:
    config = load_config()
    config["backtest"]["raw_data_dir"] = str(tmp_path / "raw")
    config["backtest"]["local_data_dir"] = str(tmp_path / "processed")
    config["backtest"]["manifest_dir"] = str(tmp_path / "manifests")
    config["backtest"]["preparation_sample_dir"] = str(tmp_path / "sample_raw")
    config["backtest"]["output_dir"] = str(tmp_path / "backtest_outputs")
    config["paths"]["records_dir"] = str(tmp_path / "records")
    config["paths"]["reports_dir"] = str(tmp_path / "reports")
    config["paths"]["examples_dir"] = str(tmp_path / "examples")
    return config


def _write_raw_daily_bars(raw_dir: Path, code: str = "300201") -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    with (raw_dir / "daily_bars.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "trade_date", "code", "name", "open", "high", "low", "close",
            "volume", "amount", "turnover_pct", "float_mcap_yi",
            "limit_up", "limit_down", "is_st", "is_suspended", "list_date",
        ])
        writer.writerows([
            ["2025-01-02", code, "Prepared Stock", "9.40", "9.60", "9.30", "9.50", "100", "200000000", "7", "100", "10.45", "8.55", "false", "false", "2020-01-01"],
            ["2025-01-03", code, "Prepared Stock", "9.50", "9.70", "9.40", "9.60", "100", "200000000", "7", "100", "10.56", "8.64", "false", "false", "2020-01-01"],
            ["2025-01-06", code, "Prepared Stock", "9.60", "9.80", "9.50", "9.70", "100", "200000000", "7", "100", "10.67", "8.73", "false", "false", "2020-01-01"],
            ["2025-01-07", code, "Prepared Stock", "9.70", "9.90", "9.60", "9.80", "100", "200000000", "7", "100", "10.78", "8.82", "false", "false", "2020-01-01"],
            ["2025-01-08", code, "Prepared Stock", "9.80", "10.00", "9.70", "9.90", "100", "200000000", "7", "100", "10.89", "8.91", "false", "false", "2020-01-01"],
            ["2025-01-09", code, "Prepared Stock", "9.90", "10.30", "9.90", "10.20", "180", "300000000", "8", "100", "11.22", "9.18", "false", "false", "2020-01-01"],
            ["2025-01-10", code, "Prepared Stock", "10.20", "10.60", "10.10", "10.50", "190", "320000000", "8", "100", "11.55", "9.45", "false", "false", "2020-01-01"],
        ])
```

- [ ] **Step 2: Write validation and dry-run tests before production modules exist**

```python
def test_prepare_dry_run_writes_no_outputs(tmp_path):
    from overnight_quant.backtest.data_preparation import PreparationRequest, prepare_dataset

    config = _config(tmp_path)
    _write_raw_daily_bars(Path(config["backtest"]["raw_data_dir"]))
    result = prepare_dataset(PreparationRequest(
        source="local-raw",
        codes=["300201"],
        start="2025-01-01",
        end="2025-01-31",
        out_dir=Path(config["backtest"]["local_data_dir"]),
        raw_dir=Path(config["backtest"]["raw_data_dir"]),
        manifest_dir=Path(config["backtest"]["manifest_dir"]),
        dry_run=True,
    ))
    assert result.status == "DRY_RUN"
    assert not Path(config["backtest"]["local_data_dir"]).exists()
    assert not Path(config["backtest"]["manifest_dir"]).exists()


def test_prepare_requires_codes(tmp_path):
    import pytest
    from overnight_quant.backtest.data_preparation import DataPreparationError, PreparationRequest, prepare_dataset

    config = _config(tmp_path)
    with pytest.raises(DataPreparationError, match="CODES_REQUIRED") as exc_info:
        prepare_dataset(PreparationRequest(
            source="sample", codes=[], start="2025-01-01", end="2025-01-31",
            out_dir=Path(config["backtest"]["local_data_dir"]),
            raw_dir=Path(config["backtest"]["raw_data_dir"]),
            manifest_dir=Path(config["backtest"]["manifest_dir"]),
            sample_dir=Path(config["backtest"]["preparation_sample_dir"]),
        ))
    assert exc_info.value.code == "CODES_REQUIRED"


def test_prepare_requires_valid_date_range(tmp_path):
    import pytest
    from overnight_quant.backtest.data_preparation import DataPreparationError, PreparationRequest, prepare_dataset

    config = _config(tmp_path)
    with pytest.raises(DataPreparationError, match="DATE_RANGE_REQUIRED") as exc_info:
        prepare_dataset(PreparationRequest(
            source="sample", codes=["300201"], start="", end="",
            out_dir=Path(config["backtest"]["local_data_dir"]),
            raw_dir=Path(config["backtest"]["raw_data_dir"]),
            manifest_dir=Path(config["backtest"]["manifest_dir"]),
            sample_dir=Path(config["backtest"]["preparation_sample_dir"]),
        ))
    assert exc_info.value.code == "DATE_RANGE_REQUIRED"
```

- [ ] **Step 3: Write output, audit, source error, and downstream-integration tests**

```python
def test_sample_source_writes_provider_loadable_processed_dataset(tmp_path):
    from overnight_quant.backtest.data_preparation import PreparationRequest, prepare_dataset

    config = _config(tmp_path)
    _write_raw_daily_bars(Path(config["backtest"]["preparation_sample_dir"]))
    result = prepare_dataset(PreparationRequest(
        source="sample", codes=["300201"], start="2025-01-01", end="2025-01-31",
        out_dir=Path(config["backtest"]["local_data_dir"]),
        raw_dir=Path(config["backtest"]["raw_data_dir"]),
        manifest_dir=Path(config["backtest"]["manifest_dir"]),
        sample_dir=Path(config["backtest"]["preparation_sample_dir"]),
        overwrite=True,
    ))
    assert result.status == "PREPARE_COMPLETED"
    for filename in (
        "daily_bars.csv", "selection_snapshots.csv", "market_snapshots.csv",
        "benchmark_bars.csv", "dataset_manifest.yaml",
    ):
        assert (result.out_dir / filename).exists()
    backtest = run_backtest(
        dataset="local", fidelity="daily_proxy", data_dir=str(result.out_dir),
        run_id="prepared-dataset", config=config,
    )
    assert "error" not in backtest
    assert Path(backtest["output_dir"]).parent == Path(config["backtest"]["output_dir"])


def test_local_raw_manifest_discloses_proxy_and_unavailable_fields(tmp_path):
    from overnight_quant.backtest.data_preparation import PreparationRequest, prepare_dataset

    config = _config(tmp_path)
    _write_raw_daily_bars(Path(config["backtest"]["raw_data_dir"]))
    result = prepare_dataset(PreparationRequest(
        source="local-raw", codes=["300201"], start="2025-01-01", end="2025-01-31",
        out_dir=Path(config["backtest"]["local_data_dir"]),
        raw_dir=Path(config["backtest"]["raw_data_dir"]),
        manifest_dir=Path(config["backtest"]["manifest_dir"]),
        overwrite=True,
    ))
    manifest = (result.out_dir / "dataset_manifest.yaml").read_text(encoding="utf-8")
    assert "fidelity: daily_proxy" in manifest
    assert "daily_range_position_proxy" in manifest
    assert "volume_ratio_proxy" in manifest
    assert "NO_INTRADAY_TAIL_DATA_IF_APPLICABLE" in manifest
    assert "NO_HISTORICAL_THEME_IF_APPLICABLE" in manifest
    assert "NO_HISTORICAL_FUND_FLOW_IF_APPLICABLE" in manifest
```

Add focused tests for overwrite refusal, exact coverage rows, a malformed
second code recorded in `source_errors.csv` with
`PARTIAL_DATA_PREPARED`, chronological rolling values, no writes under
real/example trading paths, and forbidden automation strings.

- [ ] **Step 4: Run the new tests to observe RED**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b_preparation.py -q
```

Expected: FAIL with `ModuleNotFoundError` when the first contract test invokes
`overnight_quant.backtest.data_preparation`, which does not yet exist.

- [ ] **Step 5: Commit the failing contract tests only**

```text
git add -- overnight_quant/tests/test_phase32b_preparation.py
git commit -m "Test offline historical preparation contract"
```

## Task 2: Add Offline Source Adapters And Deterministic Sample Input

**Files:**
- Create: `overnight_quant/backtest/preparation_sources.py`
- Create: `overnight_quant/examples/historical_prepare_raw/codes.txt`
- Create: `overnight_quant/examples/historical_prepare_raw/daily_bars.csv`
- Create: `overnight_quant/examples/historical_prepare_raw/benchmark_bars.csv`
- Create: `overnight_quant/tests/test_phase32b_preparation_sources.py`

- [ ] **Step 1: Extend tests for source selection, date/code bounding, and no network path**

Add tests that call a source loader directly:

```python
from overnight_quant.backtest.preparation_sources import LocalRawSource, SamplePreparationSource
from overnight_quant.tests.test_phase32b_preparation import _write_raw_daily_bars


def test_local_raw_source_limits_rows_to_codes_and_dates(tmp_path):
    raw_dir = tmp_path / "raw"
    _write_raw_daily_bars(raw_dir, "300201")
    batch = LocalRawSource(raw_dir).load(["300201"], "2025-01-08", "2025-01-10")
    assert {row["trade_date"] for row in batch.daily_rows} == {"2025-01-08", "2025-01-09", "2025-01-10"}
    assert {row["code"] for row in batch.daily_rows} == {"300201"}


def test_sample_source_reads_committed_fixture_without_live_client():
    batch = SamplePreparationSource(Path("overnight_quant/examples/historical_prepare_raw")).load(
        ["300201"], "2025-01-01", "2025-01-31"
    )
    assert batch.daily_rows
    assert {row["code"] for row in batch.daily_rows} == {"300201"}
```

- [ ] **Step 2: Run source tests to verify they fail**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b_preparation_sources.py -q
```

Expected: FAIL because `preparation_sources.py` is absent.

- [ ] **Step 3: Implement source result and offline adapter boundary**

Create `preparation_sources.py` with no imports from
`overnight_quant.data.astock_client`:

```python
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SourceError:
    source: str
    code: str
    trade_date: str
    error_code: str
    detail: str
    recoverable: bool = True


@dataclass
class SourceBatch:
    daily_rows: list[dict] = field(default_factory=list)
    benchmark_rows: list[dict] = field(default_factory=list)
    errors: list[SourceError] = field(default_factory=list)


class LocalRawSource:
    name = "local-raw"

    def __init__(self, raw_dir: str | Path):
        self.raw_dir = Path(raw_dir)

    def load(self, codes: list[str], start: str, end: str) -> SourceBatch:
        daily_rows, errors = _read_rows_for_codes(self.raw_dir / "daily_bars.csv", codes, start, end, self.name)
        benchmark_rows = _read_optional_date_rows(self.raw_dir / "benchmark_bars.csv", start, end)
        return SourceBatch(daily_rows=daily_rows, benchmark_rows=benchmark_rows, errors=errors)


class SamplePreparationSource(LocalRawSource):
    name = "sample"
```

Implement `_read_rows_for_codes` so malformed rows are retained as
`SourceError(error_code="RAW_ROW_INVALID")` for the relevant code while other
codes continue, and `_read_optional_date_rows` so missing benchmark input
returns an empty list rather than calling any other source.

- [ ] **Step 4: Create the committed sample raw fixture**

Write a tiny dataset for `300201` with at least seven dated bars in January
2025, valid safety values, sufficient volume/liquidity, and a positive
benchmark direction on the candidate day. It should allow generation of
rolling proxies and allow the downstream daily-proxy run to complete without
claiming any theme/fund/tail history.

`codes.txt`:

```text
300201
```

`daily_bars.csv` must contain the raw source columns:

```text
trade_date,code,name,open,high,low,close,volume,amount,turnover_pct,float_mcap_yi,limit_up,limit_down,is_st,is_suspended,list_date,is_limit_down
```

`benchmark_bars.csv` must contain:

```text
trade_date,open,high,low,close
```

- [ ] **Step 5: Run the focused source tests**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b_preparation_sources.py -q
```

Expected: source adapter tests pass while the service contract in
`test_phase32b_preparation.py` deliberately remains red until Task 3.

- [ ] **Step 6: Commit offline sources and committed fixture**

```text
git add -- overnight_quant/backtest/preparation_sources.py overnight_quant/examples/historical_prepare_raw overnight_quant/tests/test_phase32b_preparation_sources.py
git commit -m "Add offline preparation source adapters"
```

## Task 3: Implement Validation, Transformations, And Processed Writers

**Files:**
- Create: `overnight_quant/backtest/data_preparation.py`
- Modify: `overnight_quant/tests/test_phase32b_preparation.py`

- [ ] **Step 1: Add failing transformation tests with exact expected proxy values**

```python
import csv

from overnight_quant.backtest.data_preparation import DataPreparationError, PreparationRequest, prepare_dataset


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_prepare_calculates_only_historical_daily_proxies(tmp_path):
    config = _config(tmp_path)
    raw_dir = Path(config["backtest"]["raw_data_dir"])
    _write_raw_daily_bars(raw_dir)
    result = prepare_dataset(PreparationRequest(
        source="local-raw",
        codes=["300201"],
        start="2025-01-01",
        end="2025-01-31",
        raw_dir=raw_dir,
        out_dir=Path(config["backtest"]["local_data_dir"]),
        manifest_dir=Path(config["backtest"]["manifest_dir"]),
        overwrite=True,
    ))
    selections = _read_csv(Path(result.out_dir) / "selection_snapshots.csv")
    candidate = next(row for row in selections if row["trade_date"] == "2025-01-09")
    assert candidate["range_position"] == "0.75"
    assert candidate["vol_ratio"] == "1.8"
    assert candidate["tail_pullback_pct"] == ""
    assert candidate["theme_tags"] == ""
    daily = _read_csv(Path(result.out_dir) / "daily_bars.csv")
    candidate_bar = next(row for row in daily if row["trade_date"] == "2025-01-09")
    assert candidate_bar["change_pct"] == "3.0303"
    assert candidate_bar["ma5"] != ""
```

Also create a raw input column `next_day_theme=FutureValue` in a fixture
variant and assert it is absent from every processed selection header/value.

- [ ] **Step 2: Run the transformation tests to observe RED**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b_preparation.py -q
```

Expected: FAIL because `data_preparation.py` does not exist.

- [ ] **Step 3: Implement request/result/error models and validation**

Create `data_preparation.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from overnight_quant.backtest.preparation_sources import LocalRawSource, SamplePreparationSource, SourceBatch, SourceError


class DataPreparationError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass
class PreparationRequest:
    source: str
    codes: list[str]
    start: str
    end: str
    out_dir: Path
    raw_dir: Path
    manifest_dir: Path
    sample_dir: Path | None = None
    max_codes: int = 50
    sleep: float = 0.2
    overwrite: bool = False
    dry_run: bool = False


@dataclass
class PreparationResult:
    status: str
    out_dir: Path
    processed_files: dict[str, str] = field(default_factory=dict)
    audit_files: dict[str, str] = field(default_factory=dict)
    code_count: int = 0
    trade_date_count: int = 0
    errors: list[SourceError] = field(default_factory=list)
```

Validation rules in `_validate_request(request)`:

```python
if not request.codes:
    raise DataPreparationError("CODES_REQUIRED")
try:
    start = date.fromisoformat(request.start)
    end = date.fromisoformat(request.end)
except ValueError as exc:
    raise DataPreparationError("DATE_RANGE_REQUIRED", str(exc)) from exc
if start > end:
    raise DataPreparationError("DATE_RANGE_REQUIRED", "start_after_end")
if request.source not in {"sample", "local-raw"}:
    raise DataPreparationError("SOURCE_NOT_IMPLEMENTED", request.source)
prepared_names = {"daily_bars.csv", "selection_snapshots.csv", "market_snapshots.csv", "benchmark_bars.csv", "dataset_manifest.yaml"}
if not request.overwrite and request.out_dir.exists() and any((request.out_dir / name).exists() for name in prepared_names):
    raise DataPreparationError("DATA_DIR_EXISTS_WITHOUT_OVERWRITE", str(request.out_dir))
```

Resolve normalized six-digit codes in stable input order and truncate to
`request.max_codes` before loading any source rows.

- [ ] **Step 4: Implement chronological normalization and safe proxy functions**

Implement these helpers, calling them only after rows are sorted by
`(code, trade_date)`:

```python
def _safe_float(value: object) -> float | None:
    try:
        return None if value in (None, "", "-") else float(value)
    except (TypeError, ValueError):
        return None


def _calculate_change_pct(close: float | None, prior_close: float | None) -> float | None:
    if close is None or prior_close in (None, 0):
        return None
    return round((close / prior_close - 1) * 100, 4)


def _calculate_range_position(close: float | None, high: float | None, low: float | None) -> float | None:
    if close is None or high is None or low is None or high <= low:
        return None
    return round((close - low) / (high - low), 4)


def _calculate_vol_ratio(volume: float | None, earlier_volumes: list[float]) -> float | None:
    prior = earlier_volumes[-5:]
    if volume is None or not prior or sum(prior) <= 0:
        return None
    return round(volume / (sum(prior) / len(prior)), 4)


def _rolling_close_indicator(closes: list[float], window: int) -> float | None:
    if not closes:
        return None
    values = closes[-window:]
    return round(sum(values) / len(values), 4)
```

For each normalized row:

- copy known raw safety fields without inventing unknown values;
- derive `is_bj_stock` from code prefixes `4` and `8` and mark
  `code_prefix_proxy`;
- compute `change_pct` only from an earlier same-code bar if source did not
  already supply it;
- compute `ma5`, `ma10`, `ma20`, `vol_ratio`, and `range_position` from
  same-code rows ending at that day;
- output blank tail/theme/capital fields unless an allowed historical raw field
  was explicitly supplied in a future approved extension;
- do not copy keys prefixed with `next_day_`.

- [ ] **Step 5: Implement processed CSV output and generated market proxy**

Use constant headers exactly matching the schema above. Write all five files,
including empty-data header-only optional files when necessary. For benchmark
rows, emit `market_snapshots.csv` using only matching-date benchmark daily
direction. If there are no benchmark rows, write a header-only market file;
do not emit a passing gate.

For YAML output, prefer `yaml.safe_dump(..., allow_unicode=True, sort_keys=False)`
if `yaml` is already installed; include a minimal deterministic YAML writer
fallback so preparation does not require a new runtime dependency. The initial
manifest already includes fidelity, fixed warning strings, proxy fields, and
unavailable optional fields asserted by the Task 1 contract; Task 4 adds the
measured coverage and source-error audit around it.

- [ ] **Step 6: Run focused tests and commit transformation layer**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b_preparation.py -q
```

Expected: validation and transform tests pass; any CLI or audit-format tests
not yet implemented remain failing.

Commit exact files only:

```text
git add -- overnight_quant/backtest/data_preparation.py overnight_quant/tests/test_phase32b_preparation.py
git commit -m "Add deterministic historical preparation transforms"
```

## Task 4: Generate Manifest, Coverage, Source Errors, And Prepare Report

**Files:**
- Modify: `overnight_quant/backtest/data_preparation.py`
- Modify: `overnight_quant/tests/test_phase32b_preparation.py`

- [ ] **Step 1: Add failing assertions for audit output**

```python
def _append_invalid_code_row(path: Path, code: str) -> None:
    with path.open("a", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow(
            ["2025-01-09", code, "Bad Row", "", "10", "9", "9.5",
             "100", "200000000", "7", "100", "", "", "", "", ""]
        )


def test_prepare_writes_manifest_coverage_and_source_error_audit(tmp_path):
    from overnight_quant.backtest.data_preparation import PreparationRequest, prepare_dataset

    config = _config(tmp_path)
    raw_dir = Path(config["backtest"]["raw_data_dir"])
    _write_raw_daily_bars(raw_dir)
    _append_invalid_code_row(raw_dir / "daily_bars.csv", code="600999")
    result = prepare_dataset(PreparationRequest(
        source="local-raw",
        codes=["300201", "600999"],
        start="2025-01-01",
        end="2025-01-31",
        raw_dir=raw_dir,
        out_dir=Path(config["backtest"]["local_data_dir"]),
        manifest_dir=Path(config["backtest"]["manifest_dir"]),
        overwrite=True,
    ))
    assert result.status == "PARTIAL_DATA_PREPARED"
    report = Path(result.audit_files["prepare_report"]).read_text(encoding="utf-8")
    coverage = Path(result.audit_files["field_coverage"]).read_text(encoding="utf-8")
    errors = Path(result.audit_files["source_errors"]).read_text(encoding="utf-8")
    assert "not suitable for strict_historical" in report
    assert "selection_snapshots,tail_pullback_pct,0" in coverage
    assert "local-raw,600999,2025-01-09,RAW_ROW_INVALID" in errors
```

Add an overwrite test:

```python
def test_prepare_refuses_existing_processed_without_overwrite(tmp_path):
    import pytest
    from overnight_quant.backtest.data_preparation import DataPreparationError, PreparationRequest, prepare_dataset

    config = _config(tmp_path)
    out_dir = Path(config["backtest"]["local_data_dir"])
    out_dir.mkdir(parents=True)
    (out_dir / "daily_bars.csv").write_text("sentinel\n", encoding="utf-8")
    with pytest.raises(DataPreparationError, match="DATA_DIR_EXISTS_WITHOUT_OVERWRITE"):
        prepare_dataset(PreparationRequest(
            source="sample", codes=["300201"], start="2025-01-01", end="2025-01-31",
            out_dir=out_dir,
            raw_dir=Path(config["backtest"]["raw_data_dir"]),
            manifest_dir=Path(config["backtest"]["manifest_dir"]),
            sample_dir=Path(config["backtest"]["preparation_sample_dir"]),
            overwrite=False,
        ))
    assert (out_dir / "daily_bars.csv").read_text(encoding="utf-8") == "sentinel\n"
```

- [ ] **Step 2: Run audit tests and confirm failure**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b_preparation.py -q
```

Expected: FAIL because audit file content and partial-status handling are not
implemented.

- [ ] **Step 3: Implement manifest content derived from output rows**

Generate this structured manifest shape from actual row/coverage values:

```python
manifest = {
    "dataset": {
        "name": f"{request.source}_daily_proxy_{request.start}_{request.end}",
        "created_at": created_at,
        "start": request.start,
        "end": request.end,
        "codes_count": code_count,
        "trade_dates_count": trade_date_count,
        "source": request.source,
        "fidelity": "daily_proxy",
    },
    "selection_as_of": "daily_close_proxy",
    "strict_historical_supported": False,
    "sources": {
        "daily_bars": {
            "source": f"{request.source}_daily_bars",
            "fields": DAILY_BAR_FIELDS,
            "proxy_fields": ["change_pct", "is_bj_stock", "ma5", "ma10", "ma20"],
            "unavailable_fields": safety_fields_missing_in_output,
        },
        "selection_snapshots": {
            "source": "daily_bar_derivations",
            "fields": SELECTION_FIELDS,
            "proxy_fields": ["vol_ratio", "range_position"],
            "unavailable_fields": ["tail_pullback_pct", "theme_tags", "theme_rank", "main_net", "big_order_net"],
        },
        "market_snapshots": {
            "source": "benchmark_direction_proxy",
            "fields": MARKET_FIELDS,
            "proxy_fields": ["market_gate", "index_change_pct"],
            "unavailable_fields": [] if market_rows else ["market_gate"],
        },
        "benchmark_bars": {"source": f"{request.source}_benchmark", "fields": BENCHMARK_FIELDS},
    },
    "warnings": [
        "DAILY_PROXY_ONLY",
        "NOT_STRICT_HISTORICAL",
        "NO_INTRADAY_TAIL_DATA_IF_APPLICABLE",
        "NO_HISTORICAL_THEME_IF_APPLICABLE",
        "NO_HISTORICAL_FUND_FLOW_IF_APPLICABLE",
    ],
}
```

For `change_pct`, if first-date rows are blank, also record a manifest note
such as `first_observation_change_pct_unavailable`.

- [ ] **Step 4: Implement audit writers**

Use a single `created_at` and `stamp = now.strftime("%Y%m%d_%H%M%S")` per
run. Only non-dry-run preparation writes:

```text
<manifest_dir>/prepare_report_<stamp>.md
<manifest_dir>/field_coverage_<stamp>.csv
<manifest_dir>/source_errors_<stamp>.csv
```

Coverage rows use:

```python
COVERAGE_FIELDS = [
    "table", "field", "present_rows", "total_rows", "coverage_pct",
    "classification", "source_or_formula",
]
```

At minimum, classify:

```text
source
proxy
unavailable
safety_unknown
```

Source error rows use:

```python
SOURCE_ERROR_FIELDS = [
    "source", "code", "trade_date", "error_code", "detail", "recoverable",
]
```

Prepare-report headings must include:

```text
# Historical Data Preparation Report
status:
source:
requested_codes:
processed_codes:
date_range:
processed_date_range:
daily_proxy_loadable:
strict_historical_supported: false
proxy_fields:
unavailable_fields:
safety_unknown_fields:
source_error_summary:
Research Limitation: This dataset is suitable only for DAILY_PROXY research and is not suitable for strict_historical validation.
```

Return `PARTIAL_DATA_PREPARED` if source errors exist but a loadable
`daily_bars.csv` was produced; return `NO_DAILY_BARS_FETCHED` when no usable
daily row survives.

- [ ] **Step 5: Run audit tests and commit**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b_preparation.py -q
```

Expected: all service/source/audit tests pass except pending CLI and
production-string checks.

Commit:

```text
git add -- overnight_quant/backtest/data_preparation.py overnight_quant/tests/test_phase32b_preparation.py
git commit -m "Add preparation manifest and quality audit outputs"
```

## Task 5: Add The Preparation CLI And Configuration Defaults

**Files:**
- Create: `overnight_quant/scripts/prepare_backtest_data.py`
- Modify: `overnight_quant/config.yaml`
- Modify: `overnight_quant/strategy/yang_yongxing_overnight.py`
- Modify: `overnight_quant/tests/test_phase32b_preparation.py`

- [ ] **Step 1: Add failing CLI-facing tests**

Use callable `run_prepare()` for deterministic tests and `subprocess` only
for one console-output assertion:

```python
from overnight_quant.scripts.prepare_backtest_data import run_prepare


def test_a_stock_data_source_is_explicitly_deferred(tmp_path):
    result = run_prepare(
        source="a-stock-data", codes=["300201"], start="2025-01-01", end="2025-01-31",
        out_dir=str(tmp_path / "processed"), config=_config(tmp_path),
    )
    assert result["error"] == "SOURCE_NOT_IMPLEMENTED"


def test_non_dry_validation_failure_writes_prepare_report(tmp_path):
    config = _config(tmp_path)
    result = run_prepare(
        source="sample", codes=[], start="2025-01-01", end="2025-01-31",
        out_dir=config["backtest"]["local_data_dir"], dry_run=False, config=config,
    )
    assert result["error"] == "CODES_REQUIRED"
    report = Path(result["audit_files"]["prepare_report"])
    assert report.exists()
    assert "CODES_REQUIRED" in report.read_text(encoding="utf-8")


def test_max_codes_caps_requested_universe_before_reading_source(tmp_path):
    config = _config(tmp_path)
    sample_dir = Path(config["backtest"]["preparation_sample_dir"])
    _write_raw_daily_bars(sample_dir, code="300201")
    result = run_prepare(
        source="sample", codes=["300201", "600999"], start="2025-01-01", end="2025-01-31",
        out_dir=config["backtest"]["local_data_dir"], max_codes=1, overwrite=True, config=config,
    )
    assert result["requested_code_count"] == 2
    assert result["capped_codes"] == ["300201"]
```

- [ ] **Step 2: Run CLI tests to confirm RED**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b_preparation.py -q
```

Expected: FAIL because CLI routing and config defaults are not present.

- [ ] **Step 3: Implement `run_prepare()` and command-line parsing**

Create `prepare_backtest_data.py` with this public testable entry point:

```python
def run_prepare(
    source: str,
    codes: list[str] | None = None,
    codes_file: str | None = None,
    start: str = "",
    end: str = "",
    out_dir: str | None = None,
    raw_dir: str | None = None,
    max_codes: int = 50,
    sleep: float = 0.2,
    overwrite: bool = False,
    dry_run: bool = False,
    config: dict | None = None,
) -> dict:
    ...
```

Parser arguments:

```python
parser.add_argument("--source", required=True, choices=["sample", "local-raw", "a-stock-data"])
code_group = parser.add_mutually_exclusive_group()
code_group.add_argument("--codes")
code_group.add_argument("--codes-file")
parser.add_argument("--start", default="")
parser.add_argument("--end", default="")
parser.add_argument("--out-dir", default=None)
parser.add_argument("--raw-dir", default=None)
parser.add_argument("--max-codes", type=int, default=50)
parser.add_argument("--sleep", type=float, default=0.2)
parser.add_argument("--overwrite", action="store_true")
parser.add_argument("--dry-run", action="store_true")
```

`--codes-file` accepts comma-separated and newline-separated code tokens while
ignoring blank lines and lines beginning with `#`. Console behavior is stable:

```python
if result.get("error"):
    print(f"{result['error']}: {result.get('detail', '')}".rstrip())
    return 2
print(result["status"])
print(f"Output Directory: {result.get('out_dir', '')}")
for key, value in result.get("audit_files", {}).items():
    print(f"{key}: {value}")
return 0
```

For `DRY_RUN`, print the normalized/capped codes, input source, date range, and
planned paths; do not invoke writers. For a non-dry validation or source error,
catch `DataPreparationError` in `run_prepare()`, call a small
`write_failed_prepare_report(request_context, error, manifest_dir, now)`
writer in `data_preparation.py`, and return the stable error code plus the
report path. This preserves the approved audit behavior without writing any
processed file after validation failure.

- [ ] **Step 4: Add configured default paths**

Update YAML:

```yaml
backtest:
  raw_data_dir: overnight_quant/backtest_data/raw
  local_data_dir: overnight_quant/backtest_data/processed
  manifest_dir: overnight_quant/backtest_data/manifests
  preparation_sample_dir: overnight_quant/examples/historical_prepare_raw
```

Mirror those keys in the Python fallback config returned from
`overnight_quant/strategy/yang_yongxing_overnight.py`.

- [ ] **Step 5: Run tests and commit CLI/configuration**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b_preparation.py -q
```

Expected: all new Phase 3.2b-1 tests pass.

Commit:

```text
git add -- overnight_quant/scripts/prepare_backtest_data.py overnight_quant/config.yaml overnight_quant/strategy/yang_yongxing_overnight.py overnight_quant/tests/test_phase32b_preparation.py
git commit -m "Add offline historical preparation command"
```

## Task 6: Document Offline Preparation And Enforce Output Isolation

**Files:**
- Modify: `overnight_quant/backtest_data/README.md`
- Modify: `overnight_quant/README.md`
- Modify: `overnight_quant/RELEASE_NOTES.md`
- Modify: `overnight_quant/tests/test_phase32b_preparation.py`

- [ ] **Step 1: Add isolation and prohibition tests**

```python
def test_prepare_writes_only_backtest_data_and_backtest_outputs(tmp_path):
    config = _config(tmp_path)
    _write_raw_daily_bars(Path(config["backtest"]["preparation_sample_dir"]))
    result = run_prepare(
        source="sample", codes=["300201"], start="2025-01-01", end="2025-01-31",
        out_dir=config["backtest"]["local_data_dir"], overwrite=True, config=config,
    )
    run_backtest(
        dataset="local", fidelity="daily_proxy", data_dir=result["out_dir"],
        run_id="prepared-isolation", config=config,
    )
    assert not Path(config["paths"]["records_dir"]).exists()
    assert not Path(config["paths"]["reports_dir"]).exists()
    assert not Path(config["paths"]["examples_dir"]).exists()


def test_preparation_production_code_has_no_network_or_automatic_trading_imports():
    root = Path(__file__).resolve().parents[1]
    paths = [
        root / "backtest" / "data_preparation.py",
        root / "backtest" / "preparation_sources.py",
        root / "scripts" / "prepare_backtest_data.py",
    ]
    text = "\n".join(path.read_text(encoding="utf-8", errors="ignore").lower() for path in paths)
    forbidden = ["urllib", "requests", "astock_client", "pyautogui", "selenium", "broker api", "auto" + "_order", "place" + "_order"]
    assert not any(token in text for token in forbidden)
```

- [ ] **Step 2: Update documentation**

Document these commands in `overnight_quant/README.md` and
`overnight_quant/backtest_data/README.md`:

```text
python overnight_quant/scripts/prepare_backtest_data.py --source sample --codes 300201 --start 2025-01-01 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed --overwrite
python overnight_quant/scripts/run_backtest.py --dataset local --fidelity daily_proxy --data-dir overnight_quant/backtest_data/processed
```

For local raw:

```text
python overnight_quant/scripts/prepare_backtest_data.py --source local-raw --codes-file overnight_quant/backtest_data/raw/codes.txt --raw-dir overnight_quant/backtest_data/raw --start 2025-01-01 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed --overwrite
```

State prominently:

- prepared datasets and manifest reports are ignored research artifacts;
- `DAILY_PROXY` is not complete historical validation of the overnight
  strategy;
- no live data fills missing history;
- the command does not place orders or click securities software;
- `--source a-stock-data` is intentionally unavailable in Phase 3.2b-1.

Add a `v0.3.2b-1` release-note entry limited to deterministic offline
preparation.

- [ ] **Step 3: Run tests and commit documentation/isolation**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b_preparation.py -q
```

Expected: PASS.

Commit:

```text
git add -- overnight_quant/backtest_data/README.md overnight_quant/README.md overnight_quant/RELEASE_NOTES.md overnight_quant/tests/test_phase32b_preparation.py
git commit -m "Document deterministic historical preparation flow"
```

## Task 7: End-To-End Verification And Scoped Delivery Commit Review

**Files:**
- No new production files expected; adjust only files already listed above if verification reveals an implementation defect.

- [ ] **Step 1: Run the complete test suite**

Run:

```text
python -m pytest overnight_quant/tests -q
```

Expected: all existing Phase 1/2/3 tests and new Phase 3.2b-1 tests pass.

- [ ] **Step 2: Reset ignored preparation outputs for a fresh CLI verification**

Use a generated ignored dataset location, without touching real/example
trading records:

```text
python overnight_quant/scripts/prepare_backtest_data.py --source sample --codes 300201 --start 2025-01-01 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed --overwrite
```

Expected console status:

```text
PREPARE_COMPLETED
```

Verify these ignored generated files exist:

```text
overnight_quant/backtest_data/processed/daily_bars.csv
overnight_quant/backtest_data/processed/selection_snapshots.csv
overnight_quant/backtest_data/processed/market_snapshots.csv
overnight_quant/backtest_data/processed/benchmark_bars.csv
overnight_quant/backtest_data/processed/dataset_manifest.yaml
```

- [ ] **Step 3: Verify local-raw command and overwrite protection**

Run with a small raw fixture copied or prepared beneath the ignored raw
directory during verification:

```text
python overnight_quant/scripts/prepare_backtest_data.py --source local-raw --codes-file overnight_quant/backtest_data/raw/codes.txt --raw-dir overnight_quant/backtest_data/raw --start 2025-01-01 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed --overwrite
python overnight_quant/scripts/prepare_backtest_data.py --source local-raw --codes-file overnight_quant/backtest_data/raw/codes.txt --raw-dir overnight_quant/backtest_data/raw --start 2025-01-01 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed
```

Expected second status:

```text
DATA_DIR_EXISTS_WITHOUT_OVERWRITE
```

- [ ] **Step 4: Verify dry-run creates no files in a fresh target**

Run:

```text
python overnight_quant/scripts/prepare_backtest_data.py --source sample --codes 300201 --start 2025-01-01 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed/dry_run_probe --dry-run
```

Expected: status `DRY_RUN`, and
`overnight_quant/backtest_data/processed/dry_run_probe` does not exist.

- [ ] **Step 5: Verify prepared data runs through existing daily-proxy path**

Run:

```text
python overnight_quant/scripts/run_backtest.py --dataset local --fidelity daily_proxy --data-dir overnight_quant/backtest_data/processed
```

Expected:

```text
Report Fidelity: DAILY_PROXY
Scope: daily-bar proxy research only; not a complete historical strategy validation.
```

Verify backtest run artifacts appear only beneath
`overnight_quant/backtest_outputs/<run_id>/` and its summary retains the
required research limitations.

- [ ] **Step 6: Verify audit disclosures**

Inspect the latest ignored outputs:

```text
Get-Content overnight_quant/backtest_data/processed/dataset_manifest.yaml
Get-ChildItem overnight_quant/backtest_data/manifests/prepare_report_*.md | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | Get-Content
Get-ChildItem overnight_quant/backtest_data/manifests/field_coverage_*.csv | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | Get-Content
Get-ChildItem overnight_quant/backtest_data/manifests/source_errors_*.csv | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | Get-Content
```

Expected: proxy/unavailable warnings are disclosed; no text suggests
`strict_historical` or profitability validation.

- [ ] **Step 7: Verify prohibited capabilities and git scope**

Run:

```text
rg -n -i "pyautogui|selenium|broker api|auto_order|place_order|requests|urllib|astock_client" overnight_quant/backtest/data_preparation.py overnight_quant/backtest/preparation_sources.py overnight_quant/scripts/prepare_backtest_data.py
git status --short --ignored
git diff --stat
```

Expected: no preparation-code match for network or automatic-trading
capabilities; raw/processed/audit runtime products are ignored; no
task-external file is stageable or staged.

- [ ] **Step 8: Prepare the scoped implementation submission**

Stage only the implemented Phase 3.2b-1 files from Tasks 1-6, never
`git add .`, and ensure runtime data remain ignored:

```text
git diff --cached --check
git diff --cached --stat
```

The final implementation commit or review-ready series may contain only
`overnight_quant/` source, tests, documentation, configuration, ignore rules,
and the deterministic source fixture named in this plan.

## Requirements Traceability

| Approved Requirement | Planned Coverage |
| --- | --- |
| `prepare_backtest_data.py` entry point | Task 5 |
| Only `sample` and `local-raw`; no network | Tasks 2, 5, 6, 7 |
| `CODES_REQUIRED`, `DATE_RANGE_REQUIRED`, overwrite refusal, dry-run | Tasks 1, 3, 4, 5 |
| Five processed output files | Tasks 1, 3 |
| Three timestamped audit files | Task 4 |
| Deterministic sample data | Task 2 |
| Local raw simple standardization | Tasks 2, 3 |
| Proxy calculations and unavailable fields | Tasks 3, 4 |
| Conservative unknown safety behavior | Tasks 3, 4 and existing Phase 3.2a gate |
| Manifest warnings and field source declaration | Task 4 |
| Prepared data loadable by daily-proxy | Tasks 1, 7 |
| Outputs isolated from real/example state | Tasks 6, 7 |
| No automated trading/clicking | Tasks 6, 7 |
| No actual raw/processed/audit run artifacts committed | Tasks 6, 7 |

## Execution Handoff

After approval of this plan, execute it task-by-task with TDD. The suitable
execution choices are:

1. **Subagent-Driven (recommended):** execute one task at a time with fresh
   task context and review checkpoints.
2. **Inline Execution:** execute this plan in the current session with
   explicit red/green and verification checkpoints.

Neither option expands scope beyond Phase 3.2b-1.
