# Phase 3.2b-2b Mocked A-Stock-Data Adapter Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded `a-stock-data` historical-preparation skeleton that is exercised only through an injected mock client, proving validation, cache, audit, processed-output, and offline `daily_proxy` contracts without issuing real requests.

**Architecture:** Preserve the existing preparation service as the sole writer of processed datasets and add a focused `AStockHistoricalSource` behind an injected client protocol. The CLI accepts the new source contract and can validate or dry-run it, but Phase 3.2b-2b refuses any non-dry real CLI execution without an injected mock client; no HTTP/TCP implementation, live adapter import, demo fallback, or positive fixture path exists for `a-stock-data`.

**Tech Stack:** Python standard library (`argparse`, `csv`, `dataclasses`, `datetime`, `hashlib`, `json`, `pathlib`, `typing`), existing `overnight_quant` preparation and `daily_proxy` modules, `pytest`.

---

## Locked Scope And Safety Decisions

This plan implements only an injectable, mock-driven skeleton for:

```text
prepare_backtest_data.py --source a-stock-data
  -> mocked historical source rows
  -> processed CSV + audited dataset_manifest.yaml
  -> offline LocalCsvHistoricalDataProvider
  -> daily_proxy backtest
```

It does **not** implement an actual Baidu, mootdx, Eastmoney, Tencent, THS, or
other network client. A normal non-dry CLI invocation without injected test
client returns:

```text
REAL_NETWORK_DISABLED_IN_PHASE_3_2B_2B
```

This additional status keeps the design's no-real-request boundary visible
instead of silently returning empty data or substituting demo fixtures.

Hard-limit rules are immutable in this plan:

```python
LIVE_PREP_HARD_MAX_CODES = 10
ASTOCK_DEFAULT_MAX_CODES = 10
ASTOCK_MIN_SLEEP_SECONDS = 0.2

effective_max_codes = min(user_max_codes, LIVE_PREP_HARD_MAX_CODES)
```

- Input stock count greater than `effective_max_codes` returns
  `MAX_CODES_EXCEEDS_LIVE_PREP_LIMIT`.
- The service never truncates, reorders, or selects the first N codes on this
  path.
- A successful dry-run makes no client call, cache write, processed write, or
  audit write.
- A validation-failed `a-stock-data` dry-run still writes the specifically
  required failure `prepare_report` and `source_errors` audit containing
  `requested_codes_count`, `effective_max_codes`, and
  `network_requests_made: 0`; it never writes processed data or cache data.
- Existing `sample` and `local-raw` behavior, including their accepted
  deterministic tests and neutral/positive fixture contracts, is preserved.

Truth levels for the new source are exact uppercase labels:

```text
REAL_HISTORICAL
DAILY_PROXY
SAMPLE_FIXTURE
UNAVAILABLE
UNKNOWN
```

For `source=a-stock-data`, `SAMPLE_FIXTURE` is prohibited. Theme, capital, and
tail fields are `UNAVAILABLE` by default. Unconfirmed safety fields are
`UNKNOWN` and must remain blank so the existing daily-proxy risk rejection is
exercised rather than bypassed.

## File Map

| Path | Responsibility |
| --- | --- |
| `overnight_quant/backtest/astock_historical_source.py` | New injected-client protocol, JSON cache shell, mock-driven bounded source adapter, source-stage errors |
| `overnight_quant/backtest/preparation_sources.py` | Extend shared batch/error representation without importing network clients or changing offline source behavior |
| `overnight_quant/backtest/data_preparation.py` | Admit `a-stock-data`, apply source-specific hard-cap validation, accept injected source, write truth-level manifest/coverage/audit rows |
| `overnight_quant/scripts/prepare_backtest_data.py` | Parse source-specific defaults, expose injectable callable boundary for tests, block real non-dry execution in 2b |
| `overnight_quant/config.yaml` | Add ignored historical cache path and bounded preparation policy settings |
| `overnight_quant/strategy/yang_yongxing_overnight.py` | Mirror new configuration defaults for YAML fallback |
| `overnight_quant/backtest_data/.gitignore` | Ignore `cache/` contents while retaining an optional `.gitignore` marker |
| `overnight_quant/backtest_data/cache/.gitignore` | Keep future cached mock/real raw responses out of commits |
| `overnight_quant/backtest_data/README.md` | Document mocked-only skeleton, cache contract, and disabled real request boundary |
| `overnight_quant/README.md` | Add the bounded source status and daily-proxy limitation |
| `overnight_quant/RELEASE_NOTES.md` | Record Phase 3.2b-2b as mocked adapter validation only |
| `overnight_quant/tests/test_phase32b2_astock_mocked_source.py` | New validation, adapter, cache, audit, partial/all failure, provider, and isolation tests |
| `overnight_quant/tests/test_phase32b_preparation.py` | Preserve existing sample/local assertions and update shared forbidden-capability scan only if it intentionally includes the new mock-only module |

No generated cache response, processed CSV, audit report, or backtest-output
run artifact is committed.

## Contract Types To Introduce

The new module contains no concrete network implementation:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from overnight_quant.backtest.preparation_sources import SourceBatch, SourceError


LIVE_PREP_HARD_MAX_CODES = 10
ASTOCK_DEFAULT_MAX_CODES = 10
ASTOCK_MIN_SLEEP_SECONDS = 0.2
ASTOCK_ENDPOINT_VERSION = "mock_contract_v1"


class AStockHistoricalClientProtocol(Protocol):
    def fetch_daily_bars(self, code: str, start: str, end: str) -> list[dict]:
        raise NotImplementedError

    def fetch_stock_metadata(self, code: str) -> dict:
        raise NotImplementedError

    def fetch_benchmark_bars(self, symbol: str, start: str, end: str) -> list[dict]:
        raise NotImplementedError

    def fetch_fund_flow(self, code: str, start: str, end: str) -> list[dict]:
        raise NotImplementedError


@dataclass
class SourceAudit:
    requested_codes_count: int
    effective_max_codes: int
    network_requests_made: int = 0
    mock_client_calls: int = 0
    cache_hits: int = 0
    cache_writes: int = 0
    benchmark_source: str = ""
    field_truth: dict[str, str] = field(default_factory=dict)


class AStockHistoricalSource:
    name = "a-stock-data"

    def __init__(
        self,
        client: AStockHistoricalClientProtocol,
        cache_dir: Path,
        benchmark_symbol: str = "sh000300",
        use_cache: bool = True,
    ):
        self.client = client
        self.cache_dir = Path(cache_dir)
        self.benchmark_symbol = benchmark_symbol
        self.use_cache = use_cache
```

`fetch_fund_flow()` is in the protocol only to make the future admission
surface explicit. The 2b adapter does not call it and keeps `main_net` and
`big_order_net` unavailable, because no real dated fund-flow behavior is being
validated in this mock-only stage.

The existing generic batch adds an audit field, and its error record accepts
the new source-stage shape while keeping current offline rows serializable:

```python
@dataclass
class SourceError:
    source: str
    code: str
    trade_date: str = ""
    error_code: str = ""
    detail: str = ""
    recoverable: bool = True
    stage: str = ""
    error_message: str = ""
    retry_count: int = 0


@dataclass
class SourceBatch:
    daily_rows: list[dict] = field(default_factory=list)
    benchmark_rows: list[dict] = field(default_factory=list)
    errors: list[SourceError] = field(default_factory=list)
    audit: dict = field(default_factory=dict)
```

For `sample` and `local-raw`, existing audit CSV serialization remains
unchanged. For `a-stock-data`, the writer emits exactly:

```text
code,source,stage,error_code,error_message,retry_count
```

## Cache Contract To Implement

The mock adapter uses a real filesystem cache contract but mock responses only.
Its deterministic key path is:

```text
overnight_quant/backtest_data/cache/a_stock_data/
  <source>/<endpoint_version>/<symbol>/<start>_<end>/<request_hash>.json
```

The cache key helper uses only normalized request identity:

```python
def build_cache_path(
    cache_root: Path,
    source: str,
    endpoint_version: str,
    symbol: str,
    start: str,
    end: str,
    request_params: dict[str, str],
) -> Path:
    canonical = json.dumps(request_params, sort_keys=True, ensure_ascii=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return (
        cache_root
        / source
        / endpoint_version
        / symbol
        / f"{start}_{end}"
        / f"{digest}.json"
    )
```

Behavior:

- a cache hit returns parsed rows without calling the injected client;
- a cache miss calls the injected client once and writes the response;
- malformed cache JSON records `CACHE_READ_FAILED` with stage `cache_read`,
  then calls the injected client rather than falling back to sample/demo;
- client failures record stage-specific errors and do not write failed
  payloads as successful cache entries;
- no `time.sleep()` or retrying real endpoints is added in 2b; request pacing
  is validated as configuration only until a real client is separately
  reviewed.
- each injected client invocation increments `mock_client_calls`, while
  `network_requests_made` remains `0` throughout this mock-only phase.

## Task 1: Lock Source-Specific Validation With Failing Tests

**Files:**
- Create: `overnight_quant/tests/test_phase32b2_astock_mocked_source.py`

- [ ] **Step 1: Add mock client and temporary configuration helpers**

Create a fake client whose call list proves the no-network-equivalent
injection boundary:

```python
from __future__ import annotations

import csv
from pathlib import Path

from overnight_quant.scripts.prepare_backtest_data import run_prepare
from overnight_quant.scripts.run_backtest import run_backtest
from overnight_quant.strategy.yang_yongxing_overnight import load_config


class FakeHistoricalClient:
    def __init__(self, daily_by_code=None, metadata_by_code=None, benchmark=None, failures=None):
        self.daily_by_code = daily_by_code or {}
        self.metadata_by_code = metadata_by_code or {}
        self.benchmark = benchmark or []
        self.failures = failures or set()
        self.calls: list[tuple] = []

    def fetch_daily_bars(self, code, start, end):
        self.calls.append(("daily", code, start, end))
        if ("daily", code) in self.failures:
            raise RuntimeError("mock daily failure")
        return list(self.daily_by_code.get(code, []))

    def fetch_stock_metadata(self, code):
        self.calls.append(("metadata", code))
        if ("metadata", code) in self.failures:
            raise RuntimeError("mock metadata failure")
        return dict(self.metadata_by_code.get(code, {}))

    def fetch_benchmark_bars(self, symbol, start, end):
        self.calls.append(("benchmark", symbol, start, end))
        if ("benchmark", symbol) in self.failures:
            raise RuntimeError("mock benchmark failure")
        return list(self.benchmark)

    def fetch_fund_flow(self, code, start, end):
        self.calls.append(("fund_flow", code, start, end))
        raise AssertionError("fund flow must remain unavailable in mocked skeleton")


def _config(tmp_path: Path) -> dict:
    config = load_config()
    config["backtest"]["local_data_dir"] = str(tmp_path / "processed")
    config["backtest"]["manifest_dir"] = str(tmp_path / "manifests")
    config["backtest"]["historical_cache_dir"] = str(tmp_path / "cache" / "a_stock_data")
    config["backtest"]["output_dir"] = str(tmp_path / "backtest_outputs")
    config["paths"]["records_dir"] = str(tmp_path / "records")
    config["paths"]["reports_dir"] = str(tmp_path / "reports")
    config["paths"]["examples_dir"] = str(tmp_path / "examples")
    return config
```

- [ ] **Step 2: Add zero-call validation tests**

```python
def test_astock_dry_run_makes_zero_client_calls(tmp_path):
    client = FakeHistoricalClient()
    result = run_prepare(
        source="a-stock-data", codes=["300201"], start="2025-01-01", end="2025-01-31",
        out_dir=str(tmp_path / "processed"), dry_run=True, config=_config(tmp_path),
        historical_client=client,
    )
    assert result["status"] == "DRY_RUN"
    assert client.calls == []
    assert not (tmp_path / "processed").exists()
    assert not (tmp_path / "cache").exists()


def test_astock_requires_codes_before_client_use(tmp_path):
    client = FakeHistoricalClient()
    result = run_prepare(
        source="a-stock-data", codes=[], start="2025-01-01", end="2025-01-31",
        out_dir=str(tmp_path / "processed"), config=_config(tmp_path),
        historical_client=client,
    )
    assert result["error"] == "CODES_REQUIRED"
    assert client.calls == []


def test_astock_hard_limit_rejects_without_truncation_or_client_use(tmp_path):
    client = FakeHistoricalClient()
    codes = [f"3002{index:02d}" for index in range(11)]
    result = run_prepare(
        source="a-stock-data", codes=codes, start="2025-01-01", end="2025-01-31",
        out_dir=str(tmp_path / "processed"), max_codes=20, config=_config(tmp_path),
        historical_client=client,
    )
    assert result["error"] == "MAX_CODES_EXCEEDS_LIVE_PREP_LIMIT"
    assert client.calls == []
    report = Path(result["audit_files"]["prepare_report"]).read_text(encoding="utf-8")
    errors = Path(result["audit_files"]["source_errors"]).read_text(encoding="utf-8")
    assert "requested_codes_count: 11" in report
    assert "effective_max_codes: 10" in report
    assert "network_requests_made: 0" in report
    assert "MAX_CODES_EXCEEDS_LIVE_PREP_LIMIT" in errors
```

Add variants for `max_codes=2` with three requested codes and
`sleep=0.19`, asserting `MAX_CODES_EXCEEDS_LIVE_PREP_LIMIT` and
`SLEEP_BELOW_MINIMUM` respectively without any client calls.

- [ ] **Step 3: Add real-client-disabled guard test**

```python
def test_astock_cli_boundary_refuses_non_dry_execution_without_injected_client(tmp_path):
    result = run_prepare(
        source="a-stock-data", codes=["300201"], start="2025-01-01", end="2025-01-31",
        out_dir=str(tmp_path / "processed"), config=_config(tmp_path),
    )
    assert result["error"] == "REAL_NETWORK_DISABLED_IN_PHASE_3_2B_2B"
    assert not (tmp_path / "processed").exists()
```

- [ ] **Step 4: Run tests to verify RED**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2_astock_mocked_source.py -q
```

Expected: FAIL because `run_prepare()` does not accept
`historical_client`, `a-stock-data` still returns `SOURCE_NOT_IMPLEMENTED`,
and source-specific audit fields do not exist.

- [ ] **Step 5: Commit failing tests**

```text
git add -- overnight_quant/tests/test_phase32b2_astock_mocked_source.py
git commit -m "Test mocked a-stock-data preparation contract"
```

## Task 2: Implement Validation, Injection Boundary, And Failure Audit

**Files:**
- Modify: `overnight_quant/backtest/data_preparation.py`
- Modify: `overnight_quant/scripts/prepare_backtest_data.py`
- Modify: `overnight_quant/config.yaml`
- Modify: `overnight_quant/strategy/yang_yongxing_overnight.py`
- Test: `overnight_quant/tests/test_phase32b2_astock_mocked_source.py`

- [ ] **Step 1: Add source-specific constants and request fields**

In `data_preparation.py`, add constants and request context:

```python
LIVE_PREP_HARD_MAX_CODES = 10
ASTOCK_DEFAULT_MAX_CODES = 10
ASTOCK_MIN_SLEEP_SECONDS = 0.2
ASTOCK_SOURCE_ERROR_FIELDS = [
    "code", "source", "stage", "error_code", "error_message", "retry_count"
]


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
    sample_profile: str = "neutral"
    cache_dir: Path | None = None
    max_codes: int | None = 50
    sleep: float = 0.2
    overwrite: bool = False
    dry_run: bool = False
    effective_max_codes: int | None = None


class DataPreparationError(Exception):
    def __init__(self, code: str, detail: str = "", source_errors: list[SourceError] | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.source_errors = source_errors or []
```

Do not change existing `sample_profile` behavior.

- [ ] **Step 2: Implement rejecting validation for `a-stock-data` only**

Replace the present source rejection/slicing segment in `_validate_request()`
with source-specific branching:

```python
def _validate_request(request: PreparationRequest) -> list[str]:
    normalized = list(dict.fromkeys(_normalize_code(code) for code in request.codes if _normalize_code(code)))
    if not normalized:
        raise DataPreparationError("CODES_REQUIRED")
    _validate_dates(request.start, request.end)
    if request.source == "a-stock-data":
        user_max = request.max_codes if request.max_codes is not None else ASTOCK_DEFAULT_MAX_CODES
        if user_max <= 0:
            raise DataPreparationError("CODES_REQUIRED", "max_codes_must_be_positive")
        request.effective_max_codes = min(user_max, LIVE_PREP_HARD_MAX_CODES)
        if len(normalized) > request.effective_max_codes:
            raise DataPreparationError(
                "MAX_CODES_EXCEEDS_LIVE_PREP_LIMIT",
                f"requested_codes_count={len(normalized)} effective_max_codes={request.effective_max_codes}",
            )
        if request.sleep < ASTOCK_MIN_SLEEP_SECONDS:
            raise DataPreparationError("SLEEP_BELOW_MINIMUM", f"minimum={ASTOCK_MIN_SLEEP_SECONDS}")
        return normalized
    if request.source not in {"sample", "local-raw"}:
        raise DataPreparationError("SOURCE_NOT_IMPLEMENTED", request.source)
    if request.source == "sample" and request.sample_profile not in {"neutral", "positive"}:
        raise DataPreparationError("SAMPLE_PROFILE_INVALID", request.sample_profile)
    if request.max_codes is None or request.max_codes <= 0:
        raise DataPreparationError("CODES_REQUIRED", "max_codes_must_be_positive")
    if not request.dry_run and not request.overwrite and _directory_has_user_output(request.out_dir):
        raise DataPreparationError("DATA_DIR_EXISTS_WITHOUT_OVERWRITE", str(request.out_dir))
    return normalized[: request.max_codes]
```

Factor date parsing into `_validate_dates()` without altering its existing
`DATE_RANGE_REQUIRED` error code.

- [ ] **Step 3: Inject an optional historical source without constructing a network client**

Extend the service and callable CLI boundary:

```python
def prepare_dataset(
    request: PreparationRequest,
    now: datetime | None = None,
    source_override=None,
) -> PreparationResult:
    normalized_codes = _validate_request(request)
    if request.dry_run:
        return PreparationResult(
            status="DRY_RUN",
            out_dir=request.out_dir,
            requested_code_count=len(request.codes),
            capped_codes=normalized_codes,
        )
    if request.source == "a-stock-data" and source_override is None:
        raise DataPreparationError("REAL_NETWORK_DISABLED_IN_PHASE_3_2B_2B")
    source = source_override or _source_for_request(request)
    batch = source.load(normalized_codes, request.start, request.end)
    daily_rows, selection_rows = _normalize_rows(batch.daily_rows, request.source, request.sample_profile)
    if not daily_rows:
        raise DataPreparationError(
            "NO_DAILY_BARS_FETCHED", _source_error_detail(batch.errors), source_errors=batch.errors
        )
    # Keep the existing processed/audit writer sequence, passing batch.audit
    # into source-specific manifest and coverage helpers added in Task 4.


def run_prepare(
    source: str,
    sample_profile: str = "neutral",
    codes: list[str] | None = None,
    codes_file: str | None = None,
    start: str = "",
    end: str = "",
    out_dir: str | None = None,
    raw_dir: str | None = None,
    max_codes: int | None = None,
    sleep: float = 0.2,
    overwrite: bool = False,
    dry_run: bool = False,
    historical_client=None,
    config: dict | None = None,
) -> dict:
    resolved_max_codes = (
        ASTOCK_DEFAULT_MAX_CODES if source == "a-stock-data" and max_codes is None
        else (50 if max_codes is None else max_codes)
    )
    request = PreparationRequest(
        source=source,
        codes=resolved_codes,
        start=start,
        end=end,
        out_dir=_resolve_path(out_dir or backtest_config.get("local_data_dir", "overnight_quant/backtest_data/processed")),
        raw_dir=_resolve_path(raw_dir or backtest_config.get("raw_data_dir", "overnight_quant/backtest_data/raw")),
        manifest_dir=_resolve_path(backtest_config.get("manifest_dir", "overnight_quant/backtest_data/manifests")),
        sample_dir=_resolve_path(sample_dir_setting),
        cache_dir=_resolve_path(backtest_config.get(
            "historical_cache_dir", "overnight_quant/backtest_data/cache/a_stock_data"
        )),
        max_codes=resolved_max_codes,
        sleep=sleep,
        overwrite=overwrite,
        dry_run=dry_run,
    )
    source_override = None
    if source == "a-stock-data" and historical_client is not None and not dry_run:
        source_override = AStockHistoricalSource(historical_client, request.cache_dir)
    return result_to_dict(prepare_dataset(request, source_override=source_override))
```

`AStockHistoricalSource` is imported only from the new mock-only module once
Task 3 creates it. Until then, keep this step red or guard the import under
the injected-client branch.

Place source construction and `prepare_dataset()` inside the existing `try`
block. Validation happens inside `prepare_dataset()` before it emits
`REAL_NETWORK_DISABLED_IN_PHASE_3_2B_2B`, so a no-client CLI invocation with
eleven codes still returns the hard-cap rejection first.

- [ ] **Step 4: Expand failed-audit output only for the new source**

Update `write_failed_prepare_report()` so `a-stock-data` validation failures
write a report and a source error file with:

```text
requested_codes_count: <normalized requested count>
effective_max_codes: <source-specific effective maximum>
LIVE_PREP_HARD_MAX_CODES: 10
network_requests_made: 0
```

For `a-stock-data`, write:

```python
_write_csv(
    error_path,
    ASTOCK_SOURCE_ERROR_FIELDS,
    [{
        "code": "",
        "source": "a-stock-data",
        "stage": "validation",
        "error_code": error.code,
        "error_message": error.detail,
        "retry_count": 0,
    }],
)
```

Keep the old failed report behavior for sample/local and do not introduce
processed files during a failure.

Change the `run_prepare()` exception handler so a failed `a-stock-data`
dry-run writes these validation audit files, while a successful dry-run still
writes nothing:

```python
should_audit_failure = not dry_run or source == "a-stock-data"
audit_files = write_failed_prepare_report(request, exc) if should_audit_failure else {}
```

- [ ] **Step 5: Add bounded configuration defaults**

Add:

```yaml
backtest:
  historical_cache_dir: overnight_quant/backtest_data/cache/a_stock_data
  astock_default_max_codes: 10
  astock_min_sleep_seconds: 0.2
```

Mirror these values in the Python fallback config.

- [ ] **Step 6: Run validation tests to reach GREEN**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2_astock_mocked_source.py -q
python -m pytest overnight_quant/tests/test_phase32b_preparation.py -q
```

Expected: validation and no-client boundary tests pass; existing offline
preparation tests remain green.

- [ ] **Step 7: Commit validation and injection boundary**

```text
git add -- overnight_quant/backtest/data_preparation.py overnight_quant/scripts/prepare_backtest_data.py overnight_quant/config.yaml overnight_quant/strategy/yang_yongxing_overnight.py overnight_quant/tests/test_phase32b2_astock_mocked_source.py
git commit -m "Add bounded a-stock-data preparation validation"
```

## Task 3: Add The Mock-Driven Historical Source And Cache Shell

**Files:**
- Create: `overnight_quant/backtest/astock_historical_source.py`
- Modify: `overnight_quant/backtest/preparation_sources.py`
- Create: `overnight_quant/backtest_data/cache/.gitignore`
- Modify: `overnight_quant/backtest_data/.gitignore`
- Test: `overnight_quant/tests/test_phase32b2_astock_mocked_source.py`

- [ ] **Step 1: Add cache and protocol tests first**

Add deterministic source rows:

```python
def _dated_rows(code="300201", include_safety=True):
    safety = {
        "limit_up": "11.22", "limit_down": "9.18",
        "is_st": "false", "is_suspended": "false",
    } if include_safety else {}
    return [
        {
            "trade_date": "2025-01-09", "code": code, "name": "Mock Historic",
            "open": "9.90", "high": "10.30", "low": "9.90", "close": "10.20",
            "volume": "180", "amount": "300000000", "turnover_pct": "8",
            "float_mcap_yi": "", **safety,
        },
        {
            "trade_date": "2025-01-10", "code": code, "name": "Mock Historic",
            "open": "10.20", "high": "10.60", "low": "10.10", "close": "10.50",
            "volume": "190", "amount": "320000000", "turnover_pct": "8",
            "float_mcap_yi": "", **safety,
        },
    ]


def test_astock_cache_hit_avoids_second_mock_client_call(tmp_path):
    config = _config(tmp_path)
    client = FakeHistoricalClient(
        daily_by_code={"300201": _dated_rows()},
        metadata_by_code={"300201": {"name": "Mock Historic", "list_date": "2020-01-01"}},
    )
    first = run_prepare(
        source="a-stock-data", codes=["300201"], start="2025-01-01", end="2025-01-31",
        out_dir=str(tmp_path / "processed_first"), overwrite=True, config=config,
        historical_client=client,
    )
    first_calls = list(client.calls)
    second = run_prepare(
        source="a-stock-data", codes=["300201"], start="2025-01-01", end="2025-01-31",
        out_dir=str(tmp_path / "processed_second"), overwrite=True, config=config,
        historical_client=client,
    )
    assert first["status"] in {"PREPARE_COMPLETED", "PARTIAL_DATA_PREPARED"}
    assert second["status"] in {"PREPARE_COMPLETED", "PARTIAL_DATA_PREPARED"}
    assert client.calls == first_calls
    assert list((tmp_path / "cache" / "a_stock_data").rglob("*.json"))


def test_astock_invalid_cache_records_error_then_uses_mock_client(tmp_path):
    from overnight_quant.backtest.astock_historical_source import (
        ASTOCK_ENDPOINT_VERSION,
        build_cache_path,
    )

    config = _config(tmp_path)
    cache_root = Path(config["backtest"]["historical_cache_dir"])
    invalid = build_cache_path(
        cache_root,
        "daily_bars",
        ASTOCK_ENDPOINT_VERSION,
        "300201",
        "2025-01-01",
        "2025-01-31",
        {"code": "300201", "start": "2025-01-01", "end": "2025-01-31"},
    )
    invalid.parent.mkdir(parents=True, exist_ok=True)
    invalid.write_text("{not valid json", encoding="utf-8")
    client = FakeHistoricalClient(
        daily_by_code={"300201": _dated_rows()},
        metadata_by_code={"300201": {"name": "Mock Historic", "list_date": "2020-01-01"}},
    )
    result = run_prepare(
        source="a-stock-data", codes=["300201"], start="2025-01-01", end="2025-01-31",
        out_dir=str(tmp_path / "processed"), overwrite=True, config=config,
        historical_client=client,
    )
    assert "CACHE_READ_FAILED" in Path(result["audit_files"]["source_errors"]).read_text(encoding="utf-8")
    assert ("daily", "300201", "2025-01-01", "2025-01-31") in client.calls
```

The invalid-cache test obtains the deterministic cache path with the public
`build_cache_path()` helper, writes malformed JSON to it, then asserts
recovery uses only the injected mock.

- [ ] **Step 2: Run the cache tests to verify RED**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2_astock_mocked_source.py -q
```

Expected: FAIL because the cache helper and mock-driven source have not been
implemented.

- [ ] **Step 3: Add shared audit capacity to `SourceBatch`**

Append optional fields to the existing dataclasses, preserving current
constructor call sites:

```python
@dataclass
class SourceError:
    source: str
    code: str
    trade_date: str = ""
    error_code: str = ""
    detail: str = ""
    recoverable: bool = True
    stage: str = ""
    error_message: str = ""
    retry_count: int = 0


@dataclass
class SourceBatch:
    daily_rows: list[dict] = field(default_factory=list)
    benchmark_rows: list[dict] = field(default_factory=list)
    errors: list[SourceError] = field(default_factory=list)
    audit: dict = field(default_factory=dict)
```

- [ ] **Step 4: Implement protocol, cache helper, and mock-driven load path**

Create `astock_historical_source.py`. The adapter should:

1. call/cache `fetch_daily_bars()` for each exact requested code;
2. call/cache `fetch_stock_metadata()` for that same code and apply only
   `name` and stable `list_date`;
3. call/cache `fetch_benchmark_bars("sh000300", start, end)` once;
4. never invoke `fetch_fund_flow()` in this stage;
5. filter returned bars to the requested inclusive dates;
6. retain missing limit/status values as empty;
7. collect stage-specific `SourceError` rows rather than raising a whole-batch
   exception for one failed code.

Use this core pattern:

```python
def load(self, codes: list[str], start: str, end: str) -> SourceBatch:
    errors: list[SourceError] = []
    daily_rows: list[dict] = []
    audit = SourceAudit(
        requested_codes_count=len(codes),
        effective_max_codes=LIVE_PREP_HARD_MAX_CODES,
        network_requests_made=0,
        field_truth={
            "ohlcv": "REAL_HISTORICAL",
            "vol_ratio": "DAILY_PROXY",
            "range_position": "DAILY_PROXY",
            "tail_pullback_pct": "UNAVAILABLE",
            "theme_tags": "UNAVAILABLE",
            "main_net": "UNAVAILABLE",
        },
    )
    for code in codes:
        # `_load_cached_or_fetch` increments mock_client_calls only on a cache miss.
        bars = self._load_cached_or_fetch("daily_bars", code, start, end, errors, audit)
        metadata = self._load_cached_or_fetch("metadata", code, start, end, errors, audit)
        if not bars:
            errors.append(SourceError(
                source=self.name, code=code, stage="daily_bars_primary",
                error_code="HISTORICAL_DAILY_BAR_SOURCE_FAILED",
                error_message="no accepted mocked historical daily bars", retry_count=0,
            ))
            continue
        for row in bars:
            normalized = dict(row)
            normalized["code"] = code
            normalized["name"] = normalized.get("name") or metadata.get("name", "")
            normalized["list_date"] = normalized.get("list_date") or metadata.get("list_date", "")
            daily_rows.append(normalized)
    benchmark_rows = self._load_benchmark(start, end, errors, audit)
    return SourceBatch(daily_rows=daily_rows, benchmark_rows=benchmark_rows, errors=errors, audit=vars(audit))
```

Implement `_load_cached_or_fetch()` with JSON reads/writes and catch only
well-defined cache/client/response-shape exceptions, mapping them to
`CACHE_READ_FAILED`, `HISTORICAL_DAILY_BAR_SOURCE_FAILED`,
`HISTORICAL_METADATA_FAILED`, or `BENCHMARK_UNAVAILABLE`.

- [ ] **Step 5: Ignore cache artifacts**

Add:

```text
cache/*
!cache/.gitignore
```

to `overnight_quant/backtest_data/.gitignore` and create:

```text
# Cached source responses are runtime-only artifacts.
*
!.gitignore
```

in `overnight_quant/backtest_data/cache/.gitignore`.

- [ ] **Step 6: Run source/cache tests to verify GREEN**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2_astock_mocked_source.py -q
python -m pytest overnight_quant/tests/test_phase32b_preparation.py -q
```

Expected: cache miss/hit/read-failure behavior passes using only the fake
client; existing source tests remain green.

- [ ] **Step 7: Commit source skeleton and cache contract**

```text
git add -- overnight_quant/backtest/astock_historical_source.py overnight_quant/backtest/preparation_sources.py overnight_quant/backtest_data/.gitignore overnight_quant/backtest_data/cache/.gitignore overnight_quant/tests/test_phase32b2_astock_mocked_source.py
git commit -m "Add mock-driven historical source cache skeleton"
```

## Task 4: Write Truth-Level Manifest And Conservative Safety Disclosures

**Files:**
- Modify: `overnight_quant/backtest/data_preparation.py`
- Test: `overnight_quant/tests/test_phase32b2_astock_mocked_source.py`

- [ ] **Step 1: Add safety and truth-level tests**

```python
def test_astock_manifest_records_truth_levels_without_sample_fixture(tmp_path):
    result = _run_successful_astock_prepare(tmp_path, include_safety=True)
    manifest = Path(result["out_dir"], "dataset_manifest.yaml").read_text(encoding="utf-8")
    assert "REAL_HISTORICAL" in manifest
    assert "DAILY_PROXY" in manifest
    assert "UNAVAILABLE" in manifest
    assert "SAMPLE_FIXTURE: prohibited_for_a_stock_data" in manifest
    assert "sample_fixture" not in Path(result["out_dir"], "selection_snapshots.csv").read_text(encoding="utf-8")


def test_astock_missing_safety_fields_remain_unknown_and_daily_proxy_rejects(tmp_path):
    config = _config(tmp_path)
    result = _run_successful_astock_prepare(tmp_path, include_safety=False, config=config)
    processed = _read_csv(Path(result["out_dir"], "daily_bars.csv"))
    assert processed[-1]["limit_up"] == ""
    assert processed[-1]["limit_down"] == ""
    assert processed[-1]["is_st"] == ""
    assert processed[-1]["is_suspended"] == ""
    backtest = run_backtest(
        dataset="local", fidelity="daily_proxy", data_dir=result["out_dir"],
        run_id="astock-missing-safety", config=config,
    )
    rejection_text = Path(backtest["output_dir"], "rejections.csv").read_text(encoding="utf-8")
    assert "limit_price_unknown" in rejection_text
    assert "st_status_unknown" in rejection_text
    assert "suspended_status_unknown" in rejection_text
```

Use a separate metadata-missing variant to assert `list_date_missing`.

- [ ] **Step 2: Run tests to verify RED**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2_astock_mocked_source.py -q
```

Expected: truth-level and source-specific coverage/report assertions fail
until `data_preparation.py` understands the new source audit.

- [ ] **Step 3: Extend normalization without admitting enhanced fields**

Keep `_normalize_rows()` clearing historical theme/capital/tail inputs for
`a-stock-data`, exactly as it already does for non-positive input:

```python
use_sample_fixture = source == "sample" and sample_profile == "positive"
```

Do not add any `a-stock-data` condition that retains or supplies fixture,
live-theme, or current fund-flow values. The new source may provide dated
daily bars and metadata only.

- [ ] **Step 4: Add source-specific manifest metadata**

Pass `batch.audit` to `_build_manifest()` and `_build_coverage()` for
`request.source == "a-stock-data"`. Add:

```python
"requested_codes_count": len(request.codes),
"effective_max_codes": request.effective_max_codes,
"network_prepared": False,
"mock_client_prepared": True,
"backtest_network_access": False,
"truth_levels": {
    "REAL_HISTORICAL": "dated rows supplied by injected historical client contract",
    "DAILY_PROXY": "daily-row derivations",
    "SAMPLE_FIXTURE": "prohibited_for_a_stock_data",
    "UNAVAILABLE": "not reconstructed in mocked skeleton",
    "UNKNOWN": "unconfirmed required safety value",
},
"warnings": WARNING_VALUES + [
    "DAILY_PROXY_ONLY",
    "NOT_STRICT_HISTORICAL",
    "MOCK_CLIENT_ONLY_NO_REAL_NETWORK",
    "CURRENT_LIVE_VALUES_NOT_USED_FOR_HISTORICAL_BACKFILL",
],
```

The fields `tail_pullback_pct`, `theme_tags`, `theme_rank`, `main_net`, and
`big_order_net` remain unavailable. Empty `limit_up`, `limit_down`, `is_st`,
`is_suspended`, and `list_date` are classified `UNKNOWN`.

- [ ] **Step 5: Emit uppercase truth-level coverage only on new source path**

Do not churn existing neutral/positive/local coverage expectations. Extend
`_coverage_classification()` with source context so
`source=a-stock-data` emits:

```text
REAL_HISTORICAL  for accepted dated OHLCV rows or present stable list_date
DAILY_PROXY      for change_pct/is_bj_stock/MA/vol_ratio/range_position/market proxy fields
UNAVAILABLE      for theme/capital/tail fields
UNKNOWN          for missing safety fields
```

The `sample` path may retain its existing lowercase `sample_fixture`
classification because it is an earlier accepted fixture-audit contract.

- [ ] **Step 6: Run safety/truth tests to verify GREEN**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2_astock_mocked_source.py -q
python -m pytest overnight_quant/tests/test_phase32_daily_proxy.py overnight_quant/tests/test_phase32b_preparation.py -q
```

Expected: new truth-level and rejection tests pass without changing accepted
daily-proxy or fixture behavior.

- [ ] **Step 7: Commit disclosures and conservative safety handling**

```text
git add -- overnight_quant/backtest/data_preparation.py overnight_quant/tests/test_phase32b2_astock_mocked_source.py
git commit -m "Audit mocked historical field truth and safety"
```

## Task 5: Handle Partial Failure, Benchmark Proxy, And Error Audit

**Files:**
- Modify: `overnight_quant/backtest/astock_historical_source.py`
- Modify: `overnight_quant/backtest/data_preparation.py`
- Test: `overnight_quant/tests/test_phase32b2_astock_mocked_source.py`

- [ ] **Step 1: Add partial/all-failure and benchmark tests**

```python
def test_astock_partial_success_writes_processed_data_and_source_error(tmp_path):
    client = FakeHistoricalClient(
        daily_by_code={"300201": _dated_rows()},
        metadata_by_code={"300201": {"list_date": "2020-01-01"}},
        benchmark=_benchmark_rows(),
        failures={("daily", "600519")},
    )
    result = run_prepare(
        source="a-stock-data", codes=["300201", "600519"],
        start="2025-01-01", end="2025-01-31",
        out_dir=str(tmp_path / "processed"), overwrite=True, config=_config(tmp_path),
        historical_client=client,
    )
    assert result["status"] == "PARTIAL_DATA_PREPARED"
    errors = Path(result["audit_files"]["source_errors"]).read_text(encoding="utf-8")
    assert "600519,a-stock-data,daily_bars_primary,HISTORICAL_DAILY_BAR_SOURCE_FAILED" in errors
    assert Path(result["out_dir"], "daily_bars.csv").exists()


def test_astock_all_daily_failures_return_no_daily_bars(tmp_path):
    client = FakeHistoricalClient(failures={("daily", "300201")})
    result = run_prepare(
        source="a-stock-data", codes=["300201"], start="2025-01-01", end="2025-01-31",
        out_dir=str(tmp_path / "processed"), overwrite=True, config=_config(tmp_path),
        historical_client=client,
    )
    assert result["error"] == "NO_DAILY_BARS_FETCHED"
    assert not Path(tmp_path / "processed", "daily_bars.csv").exists()


def test_astock_benchmark_rows_build_disclosed_market_proxy(tmp_path):
    result = _run_successful_astock_prepare(tmp_path, benchmark=_benchmark_rows())
    rows = _read_csv(Path(result["out_dir"], "market_snapshots.csv"))
    assert rows
    assert rows[-1]["market_proxy_used"] == "true"
    assert rows[-1]["market_reason"] == "benchmark_direction_proxy"


def test_astock_benchmark_failure_does_not_invent_market_gate(tmp_path):
    config = _config(tmp_path)
    client = FakeHistoricalClient(
        daily_by_code={"300201": _dated_rows()},
        metadata_by_code={"300201": {"name": "Mock Historic", "list_date": "2020-01-01"}},
        failures={("benchmark", "sh000300")},
    )
    result = run_prepare(
        source="a-stock-data", codes=["300201"], start="2025-01-01", end="2025-01-31",
        out_dir=str(tmp_path / "processed"), overwrite=True, config=config,
        historical_client=client,
    )
    assert _read_csv(Path(result["out_dir"], "market_snapshots.csv")) == []
    backtest = run_backtest(
        dataset="local", fidelity="daily_proxy", data_dir=result["out_dir"],
        run_id="astock-benchmark-missing", config=config,
    )
    assert "market_data_unavailable" in Path(backtest["output_dir"], "skipped_days.csv").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run the new tests to verify RED**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2_astock_mocked_source.py -q
```

Expected: FAIL until stage errors, batch status, benchmark error disclosure,
and new-source CSV serialization are connected.

- [ ] **Step 3: Map source error status and audit writer**

For a successful dataset with one or more source errors, keep existing status:

```python
status = "PARTIAL_DATA_PREPARED" if batch.errors else "PREPARE_COMPLETED"
```

For no accepted daily rows, preserve:

```python
raise DataPreparationError("NO_DAILY_BARS_FETCHED", _source_error_detail(batch.errors))
```

but update failed auditing to write the a-stock normalized
`source_errors_*.csv` rows stored on
`DataPreparationError.source_errors`. Validation failures without source rows
produce one synthetic `stage=validation` row.
The audit report must display:

```text
source: a-stock-data
mock_client_prepared: true
network_requests_made: 0
mock_client_calls: <injected call count>
requested_codes_count: <count>
effective_max_codes: <effective maximum>
daily_proxy_loadable: <true/false>
strict_historical_supported: false
```

- [ ] **Step 4: Implement benchmark failure behavior**

`AStockHistoricalSource.load()` catches benchmark mock errors into:

```python
SourceError(
    source="a-stock-data",
    code="BENCHMARK",
    stage="benchmark",
    error_code="BENCHMARK_UNAVAILABLE",
    error_message=str(exc),
    retry_count=0,
)
```

and returns `benchmark_rows=[]`. Existing `_build_market_rows([])` correctly
writes no artificial market pass row; confirm this behavior rather than
altering engine policy.

- [ ] **Step 5: Run failure/benchmark tests to verify GREEN**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2_astock_mocked_source.py -q
python -m pytest overnight_quant/tests/test_phase32b_preparation.py overnight_quant/tests/test_phase32_daily_proxy.py -q
```

Expected: partial output, all-failure, and market proxy tests pass; earlier
paths remain deterministic.

- [ ] **Step 6: Commit failure and benchmark audit behavior**

```text
git add -- overnight_quant/backtest/astock_historical_source.py overnight_quant/backtest/data_preparation.py overnight_quant/tests/test_phase32b2_astock_mocked_source.py
git commit -m "Handle mocked historical preparation failures"
```

## Task 6: Verify Offline Daily-Proxy Consumption And State Isolation

**Files:**
- Modify: `overnight_quant/tests/test_phase32b2_astock_mocked_source.py`
- Modify: `overnight_quant/backtest_data/README.md`
- Modify: `overnight_quant/README.md`
- Modify: `overnight_quant/RELEASE_NOTES.md`

- [ ] **Step 1: Add provider integration and prohibition tests first**

```python
def test_astock_processed_dataset_loads_in_daily_proxy_without_trade_state_writes(tmp_path):
    config = _config(tmp_path)
    result = _run_successful_astock_prepare(tmp_path, benchmark=_benchmark_rows(), config=config)
    backtest = run_backtest(
        dataset="local", fidelity="daily_proxy", data_dir=result["out_dir"],
        run_id="mock-astock-provider-load", config=config,
    )
    assert "error" not in backtest
    summary = Path(backtest["output_dir"], "backtest_summary.md").read_text(encoding="utf-8")
    assert "Report Fidelity: DAILY_PROXY" in summary
    assert "本报告不等同于原策略完整历史验证。" in summary
    assert Path(backtest["output_dir"]).parent == Path(config["backtest"]["output_dir"])
    assert not Path(config["paths"]["records_dir"]).exists()
    assert not Path(config["paths"]["reports_dir"]).exists()
    assert not Path(config["paths"]["examples_dir"]).exists()


def test_astock_skeleton_contains_no_network_or_trading_implementation():
    root = Path(__file__).resolve().parents[1]
    paths = [
        root / "backtest" / "astock_historical_source.py",
        root / "backtest" / "data_preparation.py",
        root / "scripts" / "prepare_backtest_data.py",
    ]
    text = "\n".join(path.read_text(encoding="utf-8", errors="ignore").lower() for path in paths)
    forbidden = [
        "requests", "urllib.request", "mootdx", "qt.gtimg.cn",
        "finance.pae.baidu.com", "push2.eastmoney.com",
        "astock_client", "pyautogui", "selenium",
        "broker api", "auto" + "_order", "place" + "_order",
    ]
    assert not any(value in text for value in forbidden)
```

The protocol method names may mention `fetch_*`; they are test-injection
contracts, not a real network implementation.

- [ ] **Step 2: Run integration tests to verify RED or existing missing docs**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2_astock_mocked_source.py -q
```

Expected: behavior tests pass after Tasks 2-5; documentation/release
assertions added in this task initially fail until text is written.

- [ ] **Step 3: Document the mock-only source state**

Document:

```text
--source a-stock-data in Phase 3.2b-2b is validated through injected mock clients only.
The command has no enabled real data client and performs no real request.
LIVE_PREP_HARD_MAX_CODES is 10 and input is rejected, never truncated.
Prepared results remain DAILY_PROXY and are not strict_historical validation.
```

In `backtest_data/README.md`, include the future cache path and state that
cache files are runtime-only and ignored. In `RELEASE_NOTES.md`, add a
`v0.3.2b-2b` entry described as mock-contract validation, not a live
historical-source release.

- [ ] **Step 4: Run integration/isolation tests to verify GREEN**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2_astock_mocked_source.py -q
python -m pytest overnight_quant/tests/test_phase32b_preparation.py overnight_quant/tests/test_phase32_daily_proxy.py -q
```

Expected: all relevant tests pass; no generated file appears under real or
example trade state.

- [ ] **Step 5: Commit docs and isolation coverage**

```text
git add -- overnight_quant/tests/test_phase32b2_astock_mocked_source.py overnight_quant/backtest_data/README.md overnight_quant/README.md overnight_quant/RELEASE_NOTES.md
git commit -m "Document mocked historical adapter boundary"
```

## Task 7: Full Mock-Only Verification And Scoped Delivery Review

**Files:**
- No new production files expected; modify only files already named in this
  plan if verification uncovers a defect.

- [ ] **Step 1: Run the complete test suite**

Run:

```text
python -m pytest overnight_quant/tests -q
```

Expected: all accepted demo/live/manual/backtest/preparation tests and new
Phase 3.2b-2b mocked-source tests pass.

- [ ] **Step 2: Verify successful dry-run without a client**

Run:

```text
python overnight_quant/scripts/prepare_backtest_data.py --source a-stock-data --codes 300001,600519 --start 2025-01-01 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed --max-codes 10 --sleep 0.5 --dry-run
```

Expected console output includes:

```text
DRY_RUN
Codes: 300001,600519
Date Range: 2025-01-01 to 2025-01-31
```

There must be no new processed output, cache response, or real network
request. Existing ignored output from earlier research runs should be removed
or redirected to a temporary verification target before asserting absence.

- [ ] **Step 3: Verify hard-cap rejection through CLI without a request**

Run with eleven explicit six-digit codes and `--dry-run`:

```text
python overnight_quant/scripts/prepare_backtest_data.py --source a-stock-data --codes 300001,300002,300003,300004,300005,300006,300007,300008,300009,300010,300011 --start 2025-01-01 --end 2025-01-31 --max-codes 10 --sleep 0.5 --dry-run
```

Expected:

```text
MAX_CODES_EXCEEDS_LIVE_PREP_LIMIT
```

Inspect only the generated failure audit report/source-errors CSV; they must
show requested count `11`, effective maximum `10`, and request count `0`.

- [ ] **Step 4: Verify that real execution remains disabled**

Run:

```text
python overnight_quant/scripts/prepare_backtest_data.py --source a-stock-data --codes 300001 --start 2025-01-01 --end 2025-01-31 --max-codes 10 --sleep 0.5 --overwrite
```

Expected:

```text
REAL_NETWORK_DISABLED_IN_PHASE_3_2B_2B
```

It must not create a valid processed dataset, touch any remote source, or
fall back to sample/demo.

- [ ] **Step 5: Use pytest mock integration for processed/daily-proxy proof**

No CLI command creates a successful `a-stock-data` dataset during this phase,
because the only admitted client is injected in tests. Confirm the test
artifacts prove:

```text
FakeHistoricalClient
  -> AStockHistoricalSource cache/normalization
  -> processed CSV + manifest
  -> LocalCsvHistoricalDataProvider
  -> offline daily_proxy output
```

Expected: the integration test passes and reports retain
`DAILY_PROXY`/`NOT_STRICT_HISTORICAL` wording.

- [ ] **Step 6: Audit forbidden capabilities and git scope**

Run:

```text
rg -n -i "requests|urllib\.request|mootdx|qt\.gtimg\.cn|finance\.pae\.baidu\.com|push2\.eastmoney\.com|astock_client|pyautogui|selenium|broker api|auto_order|place_order" overnight_quant/backtest/astock_historical_source.py overnight_quant/backtest/data_preparation.py overnight_quant/scripts/prepare_backtest_data.py
git status --short --ignored --untracked-files=all
git diff --check
```

Expected: no network or automated-execution implementation in changed
production files; runtime cache/processed/audit/backtest outputs are ignored;
task-external untracked files are not staged.

- [ ] **Step 7: Stage only scoped Phase 3.2b-2b implementation files**

Never run `git add .`. Stage only files named in this plan:

```text
git add -- overnight_quant/backtest/astock_historical_source.py overnight_quant/backtest/preparation_sources.py overnight_quant/backtest/data_preparation.py overnight_quant/scripts/prepare_backtest_data.py overnight_quant/config.yaml overnight_quant/strategy/yang_yongxing_overnight.py overnight_quant/backtest_data/.gitignore overnight_quant/backtest_data/cache/.gitignore overnight_quant/backtest_data/README.md overnight_quant/README.md overnight_quant/RELEASE_NOTES.md overnight_quant/tests/test_phase32b2_astock_mocked_source.py overnight_quant/tests/test_phase32b_preparation.py
git diff --cached --check
git diff --cached --stat
```

The eventual implementation commit series must not include generated cache,
processed, audit, or backtest files, nor task-external untracked files.

## Requirements Traceability

| Approved Requirement | Planned Coverage |
| --- | --- |
| `LIVE_PREP_HARD_MAX_CODES = 10`; no truncation/reordering | Tasks 1-2, Task 7 CLI validation |
| Lower user `--max-codes` enforced; `--sleep < 0.2` rejected | Tasks 1-2 |
| Dry-run produces zero client/network calls | Tasks 1-2, Task 7 |
| Protocol-driven mock-only source, no real requests/live/demo fallback | Tasks 2-3, Task 6, Task 7 |
| Cache contract, cache read/write behavior and malformed-cache audit | Task 3 |
| Normalized `source_errors` schema and requested/effective limit audit | Tasks 1-2, Task 5 |
| Partial success and all failure behavior | Task 5 |
| Processed CSV and manifest output | Tasks 3-5 |
| Truth levels and prohibition of `SAMPLE_FIXTURE` for this source | Task 4 |
| Missing safety fields remain unknown and downstream reject | Task 4 |
| Theme/capital/tail remain unavailable and not live-filled | Tasks 3-4, Task 6 |
| Benchmark proxy and `market_data_unavailable` path | Task 5 |
| Prepared mock data readable by offline `daily_proxy` | Task 6 |
| No writes to real/example records/reports | Task 6 |
| No automated trading/clicking/real networking | Tasks 6-7 |

## Plan Self-Review

- **Scope:** Every task is limited to injected mock behavior, configuration,
  audit output, documentation, and tests. No step supplies a real client.
- **No hidden fallback:** The plan returns
  `REAL_NETWORK_DISABLED_IN_PHASE_3_2B_2B` when a non-dry CLI run has no
  injected client; sample/demo paths are never consulted.
- **Safety:** Price limits and status fields stay blank when the mock contract
  does not provide historical evidence, enabling existing risk rejection.
- **Historical fidelity:** Theme, capital, and tail fields are unavailable;
  the positive sample fixture is explicitly excluded.
- **Runtime isolation:** Processed/cache/audit/backtest outputs remain ignored,
  and no real/example trade-state path is written.
- **Implementation boundary:** The only success path for this phase is a
  pytest-injected fake client; real bounded endpoint work belongs to
  Phase 3.2b-2c after review.

## Execution Handoff

This plan is ready for review before any code is written. Once approved, the
implementation options are:

1. **Subagent-Driven (recommended):** execute one task at a time with a fresh
   implementation context and reviews between tasks.
2. **Inline Execution:** execute this plan in the current session with
   explicit red/green checkpoints and scoped commits.

Neither route performs real requests or expands beyond Phase 3.2b-2b.
