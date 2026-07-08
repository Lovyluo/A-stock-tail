# Phase 3.2b-2c Fake-Real Historical Request Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicitly enabled, tightly bounded fake-real historical preparation contract that validates the future real-request workflow without implementing or issuing any real network request.

**Architecture:** Preserve the accepted `AStockHistoricalSource` normalization/cache boundary and its existing 2b injected-mock tests. Add a separate real-request context and `AStockRealHistoricalClientProtocol` injection seam for tests only: CLI can validate an enabled request, but non-dry execution without an injected fake-real client returns `REAL_CLIENT_NOT_IMPLEMENTED_IN_PHASE_3_2C`. Processed output continues into the existing offline `daily_proxy` consumer and remains explicitly non-strict historical research.

**Tech Stack:** Python standard library (`argparse`, `dataclasses`, `datetime`, `pathlib`, `typing`), existing `overnight_quant` preparation/backtest modules, CSV/YAML text output helpers, `pytest`.

---

## Scope Lock

This implementation phase permits only:

```text
--source a-stock-data
  + optional --enable-real-astock-request validation gate
  + injected FakeAStockRealHistoricalClient in tests
  -> existing cached historical-source normalization
  -> processed CSV + manifest/audit output
  -> offline daily_proxy consumption
```

It must not add:

- a concrete HTTP or TCP real client;
- `requests`, `urllib.request`, `mootdx`, or any endpoint invocation;
- imports from the live scan adapter;
- Tencent current quote, THS current hotspot, current fund-flow, demo, or
  positive fixture fallback on the real-request path;
- networking inside a backtest;
- automatic ordering, broker integration, click automation, GUI, machine
  learning, or parameter optimization.

Phase 3.2b-2b remains a preserved contract:

```text
historical_client=<injected mock> without --enable-real-astock-request
  -> existing ten-code mocked adapter tests remain valid
```

Phase 3.2b-2c adds a separate contract:

```text
--enable-real-astock-request + real_historical_client=<injected fake-real>
  -> three-code/thirty-one-day validation and future-real-shaped audit
```

The test-only fake-real contract is not a real data source and may not be
silently reached from CLI.

## File Map

| Path | Responsibility In This Plan |
| --- | --- |
| `overnight_quant/tests/test_phase32b2c_fake_real_request.py` | New TDD coverage for enable flag, hard limits, fake-real processing, cache, audit, safety, market proxy, offline consumption, and prohibition scans |
| `overnight_quant/tests/test_phase32b2_astock_mocked_source.py` | Adjust only the CLI-without-injection status assertion while retaining the accepted 2b injected-mock contract |
| `overnight_quant/backtest/astock_historical_source.py` | Add real-request constants, protocol, cache namespace/audit mode parameters; still no concrete network client |
| `overnight_quant/backtest/data_preparation.py` | Carry real-request context, enforce three-code/thirty-one-day limits, report new errors and audit fields |
| `overnight_quant/scripts/prepare_backtest_data.py` | Add CLI flag and injectable `real_historical_client` boundary; reject every unimplemented real CLI execution before networking |
| `overnight_quant/backtest_data/README.md` | Document opt-in fake-real stage and that no real client exists yet |
| `overnight_quant/README.md` | Document CLI enable flag/error behavior and DAILY_PROXY limits |
| `overnight_quant/RELEASE_NOTES.md` | State that 2c initially validates a real-request gate with injected fakes only |

Do not create `AStockRealHistoricalClient` in this phase. Reserve that name
in documentation only for a later reviewed stage.

## Fixed Constants And Errors

Add the following source-policy constants beside the accepted 2b constants in
`overnight_quant/backtest/astock_historical_source.py`:

```python
LIVE_PREP_HARD_MAX_CODES = 10
ASTOCK_DEFAULT_MAX_CODES = 10
ASTOCK_MIN_SLEEP_SECONDS = 0.2

REAL_REQUEST_HARD_MAX_CODES = 3
REAL_REQUEST_MAX_DAYS = 31
ASTOCK_FAKE_REAL_ENDPOINT_VERSION = "fake_real_contract_v1"
```

Error contract:

```text
REAL_NETWORK_NOT_ENABLED
REAL_CLIENT_NOT_IMPLEMENTED_IN_PHASE_3_2C
MAX_CODES_EXCEEDS_REAL_REQUEST_LIMIT
DATE_RANGE_EXCEEDS_REAL_REQUEST_LIMIT
```

The existing `REAL_NETWORK_DISABLED_IN_PHASE_3_2B_2B` no longer represents a
normal no-injection CLI attempt after this phase. It may be removed from
reachable CLI behavior once its existing test is updated to
`REAL_NETWORK_NOT_ENABLED`.

Validation order for `source=a-stock-data`:

1. normalize codes; missing codes -> `CODES_REQUIRED`;
2. validate dates; invalid range -> `DATE_RANGE_REQUIRED`;
3. reject `sleep < 0.2` -> `SLEEP_BELOW_MINIMUM`;
4. if `real_request_enabled`:
   - calculate `effective_real_request_max_codes =
     min(max_codes if provided else 3, 3)`;
   - excess code count -> `MAX_CODES_EXCEEDS_REAL_REQUEST_LIMIT`;
   - inclusive date range over `31` ->
     `DATE_RANGE_EXCEEDS_REAL_REQUEST_LIMIT`;
5. otherwise retain the 2b ten-code validation for an injected
   `historical_client` or dry-run plan;
6. apply overwrite validation for non-dry output;
7. non-dry without enable and without the legacy injected mock source ->
   `REAL_NETWORK_NOT_ENABLED`;
8. non-dry with enable but without `real_historical_client` ->
   `REAL_CLIENT_NOT_IMPLEMENTED_IN_PHASE_3_2C`;
9. only an injected test client can reach the existing source normalization
   pipeline.

No validation failure constructs or calls a client.

## Contract Additions

### Request And Result Context

Extend `PreparationRequest` and `PreparationResult` in
`overnight_quant/backtest/data_preparation.py`:

```python
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
    real_request_enabled: bool = False
    request_contract: str = "offline"
    effective_real_request_max_codes: int | None = None
    requested_date_range_days: int | None = None
    max_allowed_days: int | None = None


@dataclass
class PreparationResult:
    status: str
    out_dir: Path
    processed_files: dict[str, str] = field(default_factory=dict)
    audit_files: dict[str, str] = field(default_factory=dict)
    requested_code_count: int = 0
    capped_codes: list[str] = field(default_factory=list)
    code_count: int = 0
    trade_date_count: int = 0
    errors: list[SourceError] = field(default_factory=list)
    effective_max_codes: int | None = None
    network_requests_made: int = 0
    mock_client_calls: int = 0
    real_request_enabled: bool = False
    request_contract: str = "offline"
    effective_real_request_max_codes: int | None = None
    requested_date_range_days: int | None = None
    max_allowed_days: int | None = None
    cache_hits: int = 0
    cache_writes: int = 0
```

Use request-contract labels:

```text
mocked_contract        # existing 2b injected historical_client route
fake_real_validation   # 2c enabled injected real_historical_client route
real_cli_guard         # enabled CLI with no implemented client; rejected
```

### Protocol And Audit

Do not replace the accepted `AStockHistoricalClientProtocol`. Add a matching
semantic protocol for the future-real test seam:

```python
class AStockRealHistoricalClientProtocol(Protocol):
    def fetch_daily_bars(self, code: str, start: str, end: str) -> list[dict]:
        raise NotImplementedError

    def fetch_stock_metadata(self, code: str) -> dict:
        raise NotImplementedError

    def fetch_benchmark_bars(self, symbol: str, start: str, end: str) -> list[dict]:
        raise NotImplementedError

    def fetch_fund_flow(self, code: str, start: str, end: str) -> list[dict]:
        raise NotImplementedError
```

Extend `SourceAudit`:

```python
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
    request_contract: str = "mocked_contract"
    real_request_enabled: bool = False
    fake_real_client_prepared: bool = False
    effective_real_request_max_codes: int | None = None
    requested_date_range_days: int | None = None
    max_allowed_days: int | None = None
```

Extend `AStockHistoricalSource.__init__()` without any loader change that
could introduce networking:

```python
def __init__(
    self,
    client: AStockHistoricalClientProtocol | AStockRealHistoricalClientProtocol,
    cache_dir: Path,
    effective_max_codes: int = ASTOCK_DEFAULT_MAX_CODES,
    benchmark_symbol: str = "sh000300",
    use_cache: bool = True,
    endpoint_version: str = ASTOCK_ENDPOINT_VERSION,
    request_contract: str = "mocked_contract",
    real_request_enabled: bool = False,
    effective_real_request_max_codes: int | None = None,
    requested_date_range_days: int | None = None,
    max_allowed_days: int | None = None,
):
    self.client = client
    self.cache_dir = Path(cache_dir)
    self.effective_max_codes = effective_max_codes
    self.benchmark_symbol = benchmark_symbol
    self.use_cache = use_cache
    self.endpoint_version = endpoint_version
    self.request_contract = request_contract
    self.real_request_enabled = real_request_enabled
    self.effective_real_request_max_codes = effective_real_request_max_codes
    self.requested_date_range_days = requested_date_range_days
    self.max_allowed_days = max_allowed_days
```

Use `self.endpoint_version` in `build_cache_path()` calls. The fake-real
tests therefore populate a separate cache namespace and cannot accidentally
reuse the 2b mock-contract cache.

## Task 1: Add Enable-Gate And Hard-Limit Tests First

**Files:**
- Create: `overnight_quant/tests/test_phase32b2c_fake_real_request.py`
- Modify: `overnight_quant/tests/test_phase32b2_astock_mocked_source.py`

- [ ] **Step 1: Write the fake-real client and isolated configuration fixture**

In the new test file, create a test-only client. It deliberately mirrors the
future real protocol but contains no endpoint code:

```python
from __future__ import annotations

import csv
from pathlib import Path

from overnight_quant.scripts.prepare_backtest_data import run_prepare
from overnight_quant.scripts.run_backtest import run_backtest
from overnight_quant.strategy.yang_yongxing_overnight import load_config


class FakeAStockRealHistoricalClient:
    def __init__(
        self,
        daily_by_code: dict[str, list[dict]] | None = None,
        metadata_by_code: dict[str, dict] | None = None,
        benchmark: list[dict] | None = None,
        failures: set[tuple[str, str]] | None = None,
    ):
        self.daily_by_code = daily_by_code or {}
        self.metadata_by_code = metadata_by_code or {}
        self.benchmark = benchmark or []
        self.failures = failures or set()
        self.calls: list[tuple] = []

    def fetch_daily_bars(self, code: str, start: str, end: str) -> list[dict]:
        self.calls.append(("daily", code, start, end))
        if ("daily", code) in self.failures:
            raise RuntimeError("fake-real daily failure")
        return list(self.daily_by_code.get(code, []))

    def fetch_stock_metadata(self, code: str) -> dict:
        self.calls.append(("metadata", code))
        if ("metadata", code) in self.failures:
            raise RuntimeError("fake-real metadata failure")
        return dict(self.metadata_by_code.get(code, {}))

    def fetch_benchmark_bars(self, symbol: str, start: str, end: str) -> list[dict]:
        self.calls.append(("benchmark", symbol, start, end))
        if ("benchmark", symbol) in self.failures:
            raise RuntimeError("fake-real benchmark failure")
        return list(self.benchmark)

    def fetch_fund_flow(self, code: str, start: str, end: str) -> list[dict]:
        self.calls.append(("fund_flow", code, start, end))
        raise AssertionError("fund flow is disabled in Phase 3.2b-2c")


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

- [ ] **Step 2: Write failing tests for disabled and unimplemented paths**

```python
def test_non_dry_without_enable_flag_returns_real_network_not_enabled(tmp_path):
    result = run_prepare(
        source="a-stock-data",
        codes=["600519"],
        start="2025-01-01",
        end="2025-01-10",
        out_dir=str(tmp_path / "processed"),
        max_codes=1,
        sleep=0.5,
        overwrite=True,
        config=_config(tmp_path),
    )
    assert result["error"] == "REAL_NETWORK_NOT_ENABLED"
    assert result["network_requests_made"] == 0
    assert not (tmp_path / "processed").exists()


def test_enabled_non_dry_without_real_client_returns_not_implemented(tmp_path):
    result = run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        codes=["600519"],
        start="2025-01-01",
        end="2025-01-10",
        out_dir=str(tmp_path / "processed"),
        max_codes=1,
        sleep=0.5,
        overwrite=True,
        config=_config(tmp_path),
    )
    assert result["error"] == "REAL_CLIENT_NOT_IMPLEMENTED_IN_PHASE_3_2C"
    assert result["network_requests_made"] == 0
    assert not (tmp_path / "processed").exists()
```

Update the existing assertion in
`test_phase32b2_astock_mocked_source.py`:

```python
def test_astock_cli_boundary_refuses_real_execution_without_injected_client(tmp_path):
    # existing call remains unchanged
    assert result["error"] == "REAL_NETWORK_NOT_ENABLED"
```

Do not modify the tests that supply `historical_client=client`; they preserve
the accepted mocked-only compatibility path.

- [ ] **Step 3: Write failing tests for enabled dry-run and strict limits**

```python
def test_enabled_dry_run_makes_zero_fake_real_client_calls(tmp_path):
    client = FakeAStockRealHistoricalClient()
    result = run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        real_historical_client=client,
        codes=["600519"],
        start="2025-01-01",
        end="2025-01-10",
        out_dir=str(tmp_path / "processed"),
        max_codes=1,
        sleep=0.5,
        dry_run=True,
        config=_config(tmp_path),
    )
    assert result["status"] == "DRY_RUN"
    assert result["network_requests_made"] == 0
    assert result["effective_real_request_max_codes"] == 1
    assert result["requested_date_range_days"] == 10
    assert client.calls == []
    assert not (tmp_path / "processed").exists()


def test_enabled_real_request_rejects_more_than_three_codes_without_calls(tmp_path):
    client = FakeAStockRealHistoricalClient()
    result = run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        real_historical_client=client,
        codes=["600519", "300750", "510300", "000001"],
        start="2025-01-01",
        end="2025-01-10",
        out_dir=str(tmp_path / "processed"),
        max_codes=10,
        sleep=0.5,
        dry_run=True,
        config=_config(tmp_path),
    )
    assert result["error"] == "MAX_CODES_EXCEEDS_REAL_REQUEST_LIMIT"
    assert result["network_requests_made"] == 0
    assert client.calls == []
    report = Path(result["audit_files"]["prepare_report"]).read_text(encoding="utf-8")
    assert "requested_codes_count: 4" in report
    assert "effective_real_request_max_codes: 3" in report


def test_enabled_real_request_rejects_more_than_31_days_without_calls(tmp_path):
    client = FakeAStockRealHistoricalClient()
    result = run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        real_historical_client=client,
        codes=["600519"],
        start="2025-01-01",
        end="2025-02-01",
        out_dir=str(tmp_path / "processed"),
        max_codes=1,
        sleep=0.5,
        dry_run=True,
        config=_config(tmp_path),
    )
    assert result["error"] == "DATE_RANGE_EXCEEDS_REAL_REQUEST_LIMIT"
    assert result["network_requests_made"] == 0
    assert client.calls == []
```

- [ ] **Step 4: Run tests to establish RED**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2c_fake_real_request.py overnight_quant/tests/test_phase32b2_astock_mocked_source.py -q
```

Expected failures:

```text
TypeError: run_prepare() got an unexpected keyword argument 'enable_real_astock_request'
AssertionError: REAL_NETWORK_DISABLED_IN_PHASE_3_2B_2B != REAL_NETWORK_NOT_ENABLED
```

- [ ] **Step 5: Commit the failing contract tests**

Stage only the test changes:

```text
git add -- overnight_quant/tests/test_phase32b2c_fake_real_request.py overnight_quant/tests/test_phase32b2_astock_mocked_source.py
git commit -m "Test fake-real historical request gate"
```

## Task 2: Implement Flag Parsing And Validation Without A Real Client

**Files:**
- Modify: `overnight_quant/backtest/astock_historical_source.py`
- Modify: `overnight_quant/backtest/data_preparation.py`
- Modify: `overnight_quant/scripts/prepare_backtest_data.py`
- Test: `overnight_quant/tests/test_phase32b2c_fake_real_request.py`
- Test: `overnight_quant/tests/test_phase32b2_astock_mocked_source.py`

- [ ] **Step 1: Add constants and test-only real protocol**

In `overnight_quant/backtest/astock_historical_source.py`, add:

```python
REAL_REQUEST_HARD_MAX_CODES = 3
REAL_REQUEST_MAX_DAYS = 31
ASTOCK_FAKE_REAL_ENDPOINT_VERSION = "fake_real_contract_v1"


class AStockRealHistoricalClientProtocol(Protocol):
    def fetch_daily_bars(self, code: str, start: str, end: str) -> list[dict]:
        raise NotImplementedError

    def fetch_stock_metadata(self, code: str) -> dict:
        raise NotImplementedError

    def fetch_benchmark_bars(self, symbol: str, start: str, end: str) -> list[dict]:
        raise NotImplementedError

    def fetch_fund_flow(self, code: str, start: str, end: str) -> list[dict]:
        raise NotImplementedError
```

This defines only an injectable contract. Do not implement
`AStockRealHistoricalClient`.

- [ ] **Step 2: Carry real-request validation state in preparation dataclasses**

In `overnight_quant/backtest/data_preparation.py`, import the two new limits
and add fields exactly as described under **Request And Result Context**.
Add:

```python
def _requested_date_range_days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days + 1


def _effective_real_request_max(request: PreparationRequest) -> int:
    user_max = request.max_codes if request.max_codes is not None else REAL_REQUEST_HARD_MAX_CODES
    return min(user_max, REAL_REQUEST_HARD_MAX_CODES)
```

After `_validate_dates()` succeeds, real-enabled validation becomes:

```python
if request.source == "a-stock-data" and request.real_request_enabled:
    request.request_contract = "fake_real_validation"
    request.effective_real_request_max_codes = _effective_real_request_max(request)
    request.requested_date_range_days = _requested_date_range_days(request.start, request.end)
    request.max_allowed_days = REAL_REQUEST_MAX_DAYS
    if len(normalized) > request.effective_real_request_max_codes:
        raise DataPreparationError(
            "MAX_CODES_EXCEEDS_REAL_REQUEST_LIMIT",
            (
                f"requested_codes_count={len(normalized)} "
                f"effective_real_request_max_codes={request.effective_real_request_max_codes}"
            ),
            source_audit=_empty_astock_audit(request, len(normalized)),
        )
    if request.requested_date_range_days > REAL_REQUEST_MAX_DAYS:
        raise DataPreparationError(
            "DATE_RANGE_EXCEEDS_REAL_REQUEST_LIMIT",
            (
                f"requested_date_range_days={request.requested_date_range_days} "
                f"max_allowed_days={REAL_REQUEST_MAX_DAYS}"
            ),
            source_audit=_empty_astock_audit(request, len(normalized)),
        )
```

Place `SLEEP_BELOW_MINIMUM` before this block so a request that violates
pacing is rejected before a source-specific execution gate. Structure the
existing `a-stock-data` branch with a dedicated
`if request.real_request_enabled:` branch followed by an `else:` block
containing the older ten-code mock-contract validation, so an enabled request
never also passes through that older limit.

- [ ] **Step 3: Add a post-validation source factory and distinguish execution guards**

`effective_real_request_max_codes` and date-range metadata are calculated
inside `_validate_request()`. Therefore the fake-real adapter must be
constructed after validation. Extend `prepare_dataset()`:

```python
from typing import Callable


def prepare_dataset(
    request: PreparationRequest,
    now: datetime | None = None,
    source_override: object | None = None,
    source_factory: Callable[[PreparationRequest], object] | None = None,
) -> PreparationResult:
    normalized_codes = _validate_request(request)
    if request.dry_run:
        return PreparationResult(
            status="DRY_RUN",
            out_dir=request.out_dir,
            requested_code_count=len(request.codes),
            capped_codes=normalized_codes,
            effective_max_codes=request.effective_max_codes,
            network_requests_made=0,
            real_request_enabled=request.real_request_enabled,
            request_contract=request.request_contract,
            effective_real_request_max_codes=request.effective_real_request_max_codes,
            requested_date_range_days=request.requested_date_range_days,
            max_allowed_days=request.max_allowed_days,
        )
    selected_source = source_override or (source_factory(request) if source_factory else None)
    if request.source == "a-stock-data" and selected_source is None:
        error_code = (
            "REAL_CLIENT_NOT_IMPLEMENTED_IN_PHASE_3_2C"
            if request.real_request_enabled
            else "REAL_NETWORK_NOT_ENABLED"
        )
        raise DataPreparationError(
            error_code,
            source_audit=_empty_astock_audit(request, len(normalized_codes)),
        )
    source = selected_source or _source_for_request(request)
```

This replaces the existing unqualified guard:

```python
if request.source == "a-stock-data" and source_override is None:
    error_code = (
        "REAL_CLIENT_NOT_IMPLEMENTED_IN_PHASE_3_2C"
        if request.real_request_enabled
        else "REAL_NETWORK_NOT_ENABLED"
    )
    raise DataPreparationError(
        error_code,
        source_audit=_empty_astock_audit(request, len(normalized_codes)),
    )
```

The legacy test injection still supplies `source_override` and therefore
retains its accepted success path.

- [ ] **Step 4: Add CLI flag and callable test seam**

Extend `run_prepare()` in
`overnight_quant/scripts/prepare_backtest_data.py`:

```python
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
    config: dict | None = None,
    historical_client=None,
    enable_real_astock_request: bool = False,
    real_historical_client=None,
) -> dict:
```

Resolve the default maximum by contract:

```python
resolved_max_codes = (
    REAL_REQUEST_HARD_MAX_CODES
    if source == "a-stock-data" and enable_real_astock_request and max_codes is None
    else (
        ASTOCK_DEFAULT_MAX_CODES
        if source == "a-stock-data" and max_codes is None
        else (50 if max_codes is None else max_codes)
    )
)
```

Populate request fields:

```python
real_request_enabled=source == "a-stock-data" and enable_real_astock_request,
request_contract=(
    "fake_real_validation"
    if source == "a-stock-data" and enable_real_astock_request
    else (
        "mocked_contract"
        if source == "a-stock-data" and (historical_client is not None or dry_run)
        else "real_cli_guard" if source == "a-stock-data" else "offline"
    )
),
```

Select only one injection seam. The fake-real seam is a post-validation
factory so audit fields are initialized from validated request state:

```python
source_factory = None
if source == "a-stock-data" and not dry_run:
    if enable_real_astock_request and real_historical_client is not None:
        source_factory = lambda validated: AStockHistoricalSource(
            real_historical_client,
            validated.cache_dir,
            effective_max_codes=validated.effective_real_request_max_codes,
            endpoint_version=ASTOCK_FAKE_REAL_ENDPOINT_VERSION,
            request_contract=validated.request_contract,
            real_request_enabled=validated.real_request_enabled,
            effective_real_request_max_codes=validated.effective_real_request_max_codes,
            requested_date_range_days=validated.requested_date_range_days,
            max_allowed_days=validated.max_allowed_days,
        )
    elif not enable_real_astock_request and historical_client is not None:
        source_override = AStockHistoricalSource(
            historical_client,
            request.cache_dir,
            effective_max_codes=min(resolved_max_codes, LIVE_PREP_HARD_MAX_CODES),
        )

return result_to_dict(
    prepare_dataset(
        request,
        source_override=source_override,
        source_factory=source_factory,
    )
)
```

Add parser wiring:

```python
parser.add_argument("--enable-real-astock-request", action="store_true")
```

and pass `enable_real_astock_request=args.enable_real_astock_request`.
There is deliberately no CLI path that constructs a real client.

- [ ] **Step 5: Make dry-run/error result keys visible for audits**

Add the new values to `PreparationResult` construction and the error result
dictionary:

```python
"real_request_enabled": request.real_request_enabled,
"request_contract": request.request_contract,
"effective_real_request_max_codes": request.effective_real_request_max_codes,
"requested_date_range_days": request.requested_date_range_days,
"max_allowed_days": request.max_allowed_days,
```

Update `_empty_astock_audit()` so every validation or disabled-client report
receives the same bounded-request context:

```python
def _empty_astock_audit(request: PreparationRequest, requested_count: int) -> dict:
    return {
        "requested_codes_count": requested_count,
        "effective_max_codes": request.effective_max_codes
        if request.effective_max_codes is not None
        else _astock_effective_max(request),
        "real_request_enabled": request.real_request_enabled,
        "request_contract": request.request_contract,
        "effective_real_request_max_codes": request.effective_real_request_max_codes,
        "requested_date_range_days": request.requested_date_range_days,
        "max_allowed_days": request.max_allowed_days,
        "network_requests_made": 0,
        "mock_client_calls": 0,
        "cache_hits": 0,
        "cache_writes": 0,
    }
```

For `a-stock-data` console output, print:

```python
print(f"real_request_enabled: {str(result.get('real_request_enabled', False)).lower()}")
print(f"network_requests_made: {result.get('network_requests_made', 0)}")
```

- [ ] **Step 6: Run validation tests to verify GREEN**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2c_fake_real_request.py overnight_quant/tests/test_phase32b2_astock_mocked_source.py -q
```

Expected: enable-flag, no-client and limit tests pass; the existing
`historical_client` mock integration remains green.

- [ ] **Step 7: Commit the guarded CLI/request boundary**

```text
git add -- overnight_quant/backtest/astock_historical_source.py overnight_quant/backtest/data_preparation.py overnight_quant/scripts/prepare_backtest_data.py overnight_quant/tests/test_phase32b2c_fake_real_request.py overnight_quant/tests/test_phase32b2_astock_mocked_source.py
git commit -m "Add fake-real historical request gate"
```

## Task 3: Extend The Existing Cache Adapter For Fake-Real Audit Mode

**Files:**
- Modify: `overnight_quant/backtest/astock_historical_source.py`
- Modify: `overnight_quant/backtest/data_preparation.py`
- Test: `overnight_quant/tests/test_phase32b2c_fake_real_request.py`

- [ ] **Step 1: Add deterministic fake-real dated row helpers and failing success/cache tests**

Append test fixture helpers:

```python
def _daily_rows(code: str, include_safety: bool = True) -> list[dict]:
    dates = ["2025-01-02", "2025-01-03", "2025-01-06"]
    closes = ["10.00", "10.20", "10.30"]
    rows = []
    for trade_date, close in zip(dates, closes):
        row = {
            "trade_date": trade_date,
            "code": code,
            "name": "Fake Real History",
            "open": close,
            "high": str(round(float(close) + 0.10, 2)),
            "low": str(round(float(close) - 0.10, 2)),
            "close": close,
            "volume": "100000",
            "amount": "100000000",
            "turnover_pct": "8",
            "float_mcap_yi": "100",
            "is_limit_down": "false",
        }
        if include_safety:
            row.update(
                {
                    "limit_up": "11.00",
                    "limit_down": "9.00",
                    "is_st": "false",
                    "is_suspended": "false",
                }
            )
        rows.append(row)
    return rows


def _benchmark_rows() -> list[dict]:
    return [
        {"trade_date": date, "open": "4000", "high": "4050", "low": "3990", "close": "4020"}
        for date in ["2025-01-02", "2025-01-03", "2025-01-06"]
    ]


def _client(include_safety: bool = True) -> FakeAStockRealHistoricalClient:
    return FakeAStockRealHistoricalClient(
        daily_by_code={"600519": _daily_rows("600519", include_safety)},
        metadata_by_code={"600519": {"name": "Fake Real History", "list_date": "2001-08-27"}},
        benchmark=_benchmark_rows(),
    )


def _run_fake_real_prepare(
    tmp_path: Path,
    client: FakeAStockRealHistoricalClient,
    config: dict | None = None,
    codes: list[str] | None = None,
    out_name: str = "processed",
) -> dict:
    requested = codes or ["600519"]
    return run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        real_historical_client=client,
        codes=requested,
        start="2025-01-01",
        end="2025-01-10",
        out_dir=str(tmp_path / out_name),
        max_codes=len(requested),
        sleep=0.5,
        overwrite=True,
        config=config or _config(tmp_path),
    )


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))
```

Write tests:

```python
def test_fake_real_client_prepares_processed_data_with_future_real_audit_shape(tmp_path):
    client = _client()
    result = _run_fake_real_prepare(tmp_path, client)
    manifest = Path(result["out_dir"], "dataset_manifest.yaml").read_text(encoding="utf-8")
    report = Path(result["audit_files"]["prepare_report"]).read_text(encoding="utf-8")
    assert result["status"] == "PREPARE_COMPLETED"
    assert result["network_requests_made"] == 0
    assert client.calls
    assert "real_request_enabled: true" in manifest
    assert "fake_real_client_prepared: true" in manifest
    assert "network_prepared: false" in manifest
    assert "effective_real_request_max_codes: 1" in report
    assert "requested_date_range_days: 10" in report


def test_fake_real_cache_hit_uses_separate_namespace_and_avoids_second_call(tmp_path):
    config = _config(tmp_path)
    client = _client()
    first = _run_fake_real_prepare(tmp_path, client, config=config, out_name="first")
    calls_after_first = list(client.calls)
    second = _run_fake_real_prepare(tmp_path, client, config=config, out_name="second")
    assert first["status"] == "PREPARE_COMPLETED"
    assert second["status"] == "PREPARE_COMPLETED"
    assert client.calls == calls_after_first
    cache_files = list((tmp_path / "cache" / "a_stock_data").rglob("*.json"))
    assert any("fake_real_contract_v1" in str(path) for path in cache_files)


def test_fake_real_invalid_cache_records_failure_then_refetches_injected_client(tmp_path):
    from overnight_quant.backtest.astock_historical_source import (
        ASTOCK_FAKE_REAL_ENDPOINT_VERSION,
        build_cache_path,
    )

    config = _config(tmp_path)
    invalid = build_cache_path(
        Path(config["backtest"]["historical_cache_dir"]),
        "daily_bars",
        ASTOCK_FAKE_REAL_ENDPOINT_VERSION,
        "600519",
        "2025-01-01",
        "2025-01-10",
        {"code": "600519", "start": "2025-01-01", "end": "2025-01-10"},
    )
    invalid.parent.mkdir(parents=True, exist_ok=True)
    invalid.write_text("{invalid-json", encoding="utf-8")
    client = _client()
    result = _run_fake_real_prepare(tmp_path, client, config=config)
    errors = Path(result["audit_files"]["source_errors"]).read_text(encoding="utf-8")
    assert result["status"] == "PARTIAL_DATA_PREPARED"
    assert "CACHE_READ_FAILED" in errors
    assert ("daily", "600519", "2025-01-01", "2025-01-10") in client.calls
```

- [ ] **Step 2: Run these tests to verify RED**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2c_fake_real_request.py -q
```

Expected: failure because `AStockHistoricalSource` does not yet accept the
fake-real cache/audit parameters and report fields are absent.

- [ ] **Step 3: Parameterize source audit mode and cache namespace**

Implement the constructor and `SourceAudit` changes from **Protocol And
Audit**. In `AStockHistoricalSource.load()` initialize the audit:

```python
audit = SourceAudit(
    requested_codes_count=len(codes),
    effective_max_codes=self.effective_max_codes,
    request_contract=self.request_contract,
    real_request_enabled=self.real_request_enabled,
    fake_real_client_prepared=self.request_contract == "fake_real_validation",
    effective_real_request_max_codes=self.effective_real_request_max_codes,
    requested_date_range_days=self.requested_date_range_days,
    max_allowed_days=self.max_allowed_days,
    field_truth={
        "daily_bars": "REAL_HISTORICAL",
        "vol_ratio": "DAILY_PROXY",
        "range_position": "DAILY_PROXY",
        "market_gate": "DAILY_PROXY",
        "tail_pullback_pct": "UNAVAILABLE",
        "theme_tags": "UNAVAILABLE",
        "main_net": "UNAVAILABLE",
    },
)
```

In `_cached_or_fetch()`, replace `ASTOCK_ENDPOINT_VERSION` with
`self.endpoint_version`. Keep:

```python
audit.mock_client_calls += 1
```

and leave:

```python
audit.network_requests_made == 0
```

for every fake-real test. A real client is not present.

- [ ] **Step 4: Extend manifest and prepare report for fake-real mode**

In `_build_manifest()` update only the `a-stock-data` branch:

```python
fake_real = request.request_contract == "fake_real_validation"
manifest.update(
    {
        "real_request_enabled": request.real_request_enabled,
        "request_contract": request.request_contract,
        "real_request_hard_max_codes": REAL_REQUEST_HARD_MAX_CODES if fake_real else "not_applicable",
        "effective_real_request_max_codes": request.effective_real_request_max_codes,
        "requested_date_range_days": request.requested_date_range_days,
        "max_allowed_days": request.max_allowed_days,
        "network_prepared": False,
        "mock_client_prepared": not fake_real,
        "fake_real_client_prepared": fake_real,
        "cache_hits": int(source_audit.get("cache_hits", 0)),
        "cache_writes": int(source_audit.get("cache_writes", 0)),
    }
)
```

For fake-real warnings, use:

```python
manifest["warnings"] = WARNING_VALUES + [
    "FAKE_REAL_CLIENT_ONLY_NO_NETWORK",
    "EXPERIMENTAL_SMALL_SCALE_REAL_HISTORICAL_PREPARATION_NOT_YET_ENABLED",
    "CURRENT_LIVE_VALUES_NOT_USED_FOR_HISTORICAL_BACKFILL",
]
```

Do not set `network_prepared: true`; this implementation phase never uses a
real client.

In `_write_audit_files()` and `write_failed_prepare_report()`, add:

```text
real_request_enabled: true|false
request_contract: fake_real_validation|mocked_contract|real_cli_guard
real_request_hard_max_codes: 3|not_applicable
effective_real_request_max_codes: <value or not_applicable>
requested_date_range_days: <value or not_applicable>
max_allowed_days: 31|not_applicable
cache_hits: <count>
cache_writes: <count>
```

- [ ] **Step 5: Run cache/audit tests to verify GREEN**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2c_fake_real_request.py overnight_quant/tests/test_phase32b2_astock_mocked_source.py -q
```

Expected: fake-real success and cache tests pass; 2b cache tests continue to
pass in their original namespace.

- [ ] **Step 6: Commit cache namespace and audit mode**

```text
git add -- overnight_quant/backtest/astock_historical_source.py overnight_quant/backtest/data_preparation.py overnight_quant/tests/test_phase32b2c_fake_real_request.py
git commit -m "Audit fake-real historical preparation context"
```

## Task 4: Lock Truth Levels, Safety Rejections, Failure Handling And Market Proxy

**Files:**
- Modify: `overnight_quant/tests/test_phase32b2c_fake_real_request.py`

- [ ] **Step 1: Write failing or regression tests for unavailable enhancements and safety**

Add:

```python
def test_fake_real_never_retains_theme_capital_or_tail_values(tmp_path):
    client = _client()
    client.daily_by_code["600519"][-1].update(
        {
            "theme_tags": "SHOULD_NOT_SURVIVE",
            "theme_rank": "1",
            "main_net": "999",
            "big_order_net": "888",
            "tail_pullback_pct": "0.1",
            "theme_source": "current_hot",
            "capital_source": "current_flow",
        }
    )
    result = _run_fake_real_prepare(tmp_path, client)
    selection = Path(result["out_dir"], "selection_snapshots.csv").read_text(encoding="utf-8")
    coverage = Path(result["audit_files"]["field_coverage"]).read_text(encoding="utf-8")
    assert "SHOULD_NOT_SURVIVE" not in selection
    assert "current_hot" not in selection
    assert "current_flow" not in selection
    assert "selection_snapshots,theme_tags,0,3,0.0,UNAVAILABLE" in coverage
    assert "selection_snapshots,main_net,0,3,0.0,UNAVAILABLE" in coverage
    assert "selection_snapshots,tail_pullback_pct,0,3,0.0,UNAVAILABLE" in coverage


def test_fake_real_missing_safety_fields_are_rejected_offline(tmp_path):
    config = _config(tmp_path)
    client = _client(include_safety=False)
    client.metadata_by_code["600519"] = {}
    result = _run_fake_real_prepare(tmp_path, client, config=config)
    backtest = run_backtest(
        dataset="local",
        fidelity="daily_proxy",
        data_dir=result["out_dir"],
        run_id="fake-real-missing-safety",
        config=config,
    )
    rejected = Path(backtest["output_dir"], "rejections.csv").read_text(encoding="utf-8")
    assert "limit_price_unknown" in rejected
    assert "st_status_unknown" in rejected
    assert "suspended_status_unknown" in rejected
    assert "list_date_missing" in rejected
```

These assertions protect the current clearing and truth-level behavior through
the new contract. The implementation in Tasks 2-3 must make them pass
without introducing an enrichment path.

- [ ] **Step 2: Add partial failure and all-failure fake-real tests**

```python
def test_fake_real_partial_success_writes_source_error(tmp_path):
    client = _client()
    client.daily_by_code["300750"] = _daily_rows("300750")
    client.metadata_by_code["300750"] = {"name": "Second", "list_date": "2018-06-11"}
    client.failures.add(("daily", "300750"))
    result = _run_fake_real_prepare(tmp_path, client, codes=["600519", "300750"])
    assert result["status"] == "PARTIAL_DATA_PREPARED"
    errors = Path(result["audit_files"]["source_errors"]).read_text(encoding="utf-8")
    assert "300750,a-stock-data,daily_bars_primary,HISTORICAL_DAILY_BAR_SOURCE_FAILED" in errors


def test_fake_real_all_daily_failures_return_no_dataset(tmp_path):
    client = _client()
    client.failures.add(("daily", "600519"))
    result = _run_fake_real_prepare(tmp_path, client)
    assert result["error"] == "NO_DAILY_BARS_FETCHED"
    assert not Path(result["out_dir"], "daily_bars.csv").exists()
    assert result["network_requests_made"] == 0
```

- [ ] **Step 3: Add benchmark proxy and unavailable-market tests**

```python
def test_fake_real_benchmark_rows_create_daily_market_proxy(tmp_path):
    result = _run_fake_real_prepare(tmp_path, _client())
    rows = _read_csv(Path(result["out_dir"], "market_snapshots.csv"))
    assert rows
    assert rows[-1]["market_proxy_used"] == "true"
    assert rows[-1]["market_reason"] == "benchmark_direction_proxy"


def test_fake_real_benchmark_failure_leads_to_market_data_unavailable(tmp_path):
    config = _config(tmp_path)
    client = _client()
    client.failures.add(("benchmark", "sh000300"))
    result = _run_fake_real_prepare(tmp_path, client, config=config)
    backtest = run_backtest(
        dataset="local",
        fidelity="daily_proxy",
        data_dir=result["out_dir"],
        run_id="fake-real-no-benchmark",
        config=config,
    )
    skipped = Path(backtest["output_dir"], "skipped_days.csv").read_text(encoding="utf-8")
    assert "market_data_unavailable" in skipped
```

- [ ] **Step 4: Run the truth/safety/failure/market tests**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2c_fake_real_request.py -q
```

Expected: all fake-real behavior passes through existing conservative
normalization and offline rejection rules.

- [ ] **Step 5: Commit the regression coverage**

```text
git add -- overnight_quant/tests/test_phase32b2c_fake_real_request.py
git commit -m "Cover fake-real historical safety and failure paths"
```

## Task 5: Prove Offline Consumption And Prohibited Capability Boundaries

**Files:**
- Modify: `overnight_quant/tests/test_phase32b2c_fake_real_request.py`
- Modify: `overnight_quant/backtest_data/README.md`
- Modify: `overnight_quant/README.md`
- Modify: `overnight_quant/RELEASE_NOTES.md`

- [ ] **Step 1: Add offline consumption and capability scan tests**

```python
def test_fake_real_processed_dataset_is_consumed_offline_only(tmp_path):
    config = _config(tmp_path)
    client = _client()
    prepared = _run_fake_real_prepare(tmp_path, client, config=config)
    calls_after_prepare = list(client.calls)
    backtest = run_backtest(
        dataset="local",
        fidelity="daily_proxy",
        data_dir=prepared["out_dir"],
        run_id="fake-real-provider-load",
        config=config,
    )
    assert "error" not in backtest
    assert client.calls == calls_after_prepare
    summary = Path(backtest["output_dir"], "backtest_summary.md").read_text(encoding="utf-8")
    assert "Report Fidelity: DAILY_PROXY" in summary
    assert not Path(config["paths"]["records_dir"]).exists()
    assert not Path(config["paths"]["reports_dir"]).exists()
    assert not Path(config["paths"]["examples_dir"]).exists()


def test_fake_real_phase_contains_no_real_network_or_execution_implementation():
    root = Path(__file__).resolve().parents[1]
    production_paths = [
        root / "backtest" / "astock_historical_source.py",
        root / "backtest" / "data_preparation.py",
        root / "scripts" / "prepare_backtest_data.py",
    ]
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore").lower()
        for path in production_paths
    )
    forbidden = [
        "import requests",
        "from requests",
        "urllib.request",
        "mootdx",
        "qt.gtimg.cn",
        "finance.pae.baidu.com",
        "push2.eastmoney.com",
        "astock_client",
        "pyautogui",
        "selenium",
        "broker api",
        "auto" + "_order",
        "place" + "_order",
    ]
    assert not any(token in text for token in forbidden)
```

- [ ] **Step 2: Run new tests before documentation edits**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2c_fake_real_request.py -q
```

Expected: behavior/prohibition tests pass after earlier tasks. Documentation
has not been asserted by tests because it is human-facing boundary text.

- [ ] **Step 3: Document fake-real-only behavior**

Update `overnight_quant/README.md` and
`overnight_quant/backtest_data/README.md` with this exact operational
boundary:

```text
Phase 3.2b-2c adds --enable-real-astock-request validation only.
There is no enabled real historical client in this implementation stage.
Non-dry CLI with the flag returns REAL_CLIENT_NOT_IMPLEMENTED_IN_PHASE_3_2C.
Fake-real tests exercise the future request shape without sending network traffic.
Prepared results remain DAILY_PROXY only and are not strict_historical validation.
```

Add the planned enabled dry-run example:

```text
python overnight_quant/scripts/prepare_backtest_data.py \
  --source a-stock-data \
  --enable-real-astock-request \
  --codes 600519 \
  --start 2025-01-01 \
  --end 2025-01-10 \
  --out-dir overnight_quant/backtest_data/processed \
  --max-codes 1 \
  --sleep 0.5 \
  --dry-run
```

Add a release note labelled as gate validation only, explicitly saying no
real source client and no request were shipped.

- [ ] **Step 4: Run focused tests after documentation updates**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2c_fake_real_request.py overnight_quant/tests/test_phase32b2_astock_mocked_source.py -q
```

Expected: all relevant tests pass and no code scan finds network or automatic
execution capabilities.

- [ ] **Step 5: Commit docs and isolation proof**

```text
git add -- overnight_quant/tests/test_phase32b2c_fake_real_request.py overnight_quant/README.md overnight_quant/backtest_data/README.md overnight_quant/RELEASE_NOTES.md
git commit -m "Document fake-real historical request boundary"
```

## Task 6: Full Verification For The Fake-Real Implementation Stage

**Files:**
- No new implementation files expected. Modify only a file already listed
  above if verification exposes a defect, and repeat the failing test first.

- [ ] **Step 1: Run all existing and new tests**

Run:

```text
python -m pytest overnight_quant/tests -q
```

Expected: all previously accepted demo/live/manual/backtest/preparation tests
and the new fake-real tests pass.

- [ ] **Step 2: Verify enabled dry-run is bounded and performs zero requests**

Run:

```text
python overnight_quant/scripts/prepare_backtest_data.py \
  --source a-stock-data \
  --enable-real-astock-request \
  --codes 600519 \
  --start 2025-01-01 \
  --end 2025-01-10 \
  --out-dir overnight_quant/backtest_data/processed/fake_real_dry_run_probe \
  --max-codes 1 \
  --sleep 0.5 \
  --dry-run
```

Expected console lines:

```text
DRY_RUN
real_request_enabled: true
network_requests_made: 0
Codes: 600519
Date Range: 2025-01-01 to 2025-01-10
```

The command does not write processed data or a cache payload.

- [ ] **Step 3: Verify enabled hard-code rejection without requests**

Run:

```text
python overnight_quant/scripts/prepare_backtest_data.py \
  --source a-stock-data \
  --enable-real-astock-request \
  --codes 600519,300750,510300,000001 \
  --start 2025-01-01 \
  --end 2025-01-10 \
  --out-dir overnight_quant/backtest_data/processed/fake_real_limit_probe \
  --max-codes 10 \
  --sleep 0.5 \
  --dry-run
```

Expected:

```text
MAX_CODES_EXCEEDS_REAL_REQUEST_LIMIT
network_requests_made: 0
```

Inspect its ignored audit report for:

```text
requested_codes_count: 4
effective_real_request_max_codes: 3
```

- [ ] **Step 4: Verify enabled date-range rejection without requests**

Run:

```text
python overnight_quant/scripts/prepare_backtest_data.py \
  --source a-stock-data \
  --enable-real-astock-request \
  --codes 600519 \
  --start 2025-01-01 \
  --end 2025-02-01 \
  --out-dir overnight_quant/backtest_data/processed/fake_real_date_probe \
  --max-codes 1 \
  --sleep 0.5 \
  --dry-run
```

Expected:

```text
DATE_RANGE_EXCEEDS_REAL_REQUEST_LIMIT
network_requests_made: 0
```

- [ ] **Step 5: Verify both CLI execution guards remain network-free**

Run without the enable flag:

```text
python overnight_quant/scripts/prepare_backtest_data.py \
  --source a-stock-data \
  --codes 600519 \
  --start 2025-01-01 \
  --end 2025-01-10 \
  --out-dir overnight_quant/backtest_data/processed/no_enable_probe \
  --max-codes 1 \
  --sleep 0.5 \
  --overwrite
```

Expected:

```text
REAL_NETWORK_NOT_ENABLED
network_requests_made: 0
```

Run with enable flag but without any injected fake-real client:

```text
python overnight_quant/scripts/prepare_backtest_data.py \
  --source a-stock-data \
  --enable-real-astock-request \
  --codes 600519 \
  --start 2025-01-01 \
  --end 2025-01-10 \
  --out-dir overnight_quant/backtest_data/processed/no_real_client_probe \
  --max-codes 1 \
  --sleep 0.5 \
  --overwrite
```

Expected:

```text
REAL_CLIENT_NOT_IMPLEMENTED_IN_PHASE_3_2C
network_requests_made: 0
```

- [ ] **Step 6: Verify fake-real processed/offline-daily-proxy behavior through tests only**

Do not add a CLI route that loads `FakeAStockRealHistoricalClient`. Rely on:

```text
test_fake_real_client_prepares_processed_data_with_future_real_audit_shape
test_fake_real_processed_dataset_is_consumed_offline_only
```

These prove:

```text
injected fake-real client
  -> isolated fake-real cache namespace
  -> processed files and DAILY_PROXY disclosures
  -> LocalCsvHistoricalDataProvider
  -> offline backtest outputs only
```

- [ ] **Step 7: Check prohibited dependencies and git scope**

Run:

```text
rg -n -i "import requests|from requests|urllib\.request|mootdx|qt\.gtimg\.cn|finance\.pae\.baidu\.com|push2\.eastmoney\.com|astock_client|pyautogui|selenium|broker api|auto_order|place_order" overnight_quant/backtest/astock_historical_source.py overnight_quant/backtest/data_preparation.py overnight_quant/scripts/prepare_backtest_data.py
git status --short --ignored --untracked-files=all
git diff --check
```

Expected:

- no production implementation of network access or automatic execution;
- generated cache/processed/audit/backtest outputs are ignored;
- `AGENTS.md`, `holdings.json`, and any other task-external file are not
  staged.

- [ ] **Step 8: Stage only implementation-phase files and create the release commit**

Never run `git add .`. Stage only files actually modified during this phase,
from the following approved set:

```text
git add -- overnight_quant/backtest/astock_historical_source.py overnight_quant/backtest/data_preparation.py overnight_quant/scripts/prepare_backtest_data.py overnight_quant/tests/test_phase32b2c_fake_real_request.py overnight_quant/tests/test_phase32b2_astock_mocked_source.py overnight_quant/README.md overnight_quant/backtest_data/README.md overnight_quant/RELEASE_NOTES.md
git diff --cached --check
git diff --cached --stat
git commit -m "Add fake-real historical request validation gate"
```

Do not stage a file from this set when it remained unchanged. Do not stage
any runtime product.

## Requirements Traceability

| Confirmed Requirement | Plan Coverage |
| --- | --- |
| `--enable-real-astock-request` explicit gate | Tasks 1-2; Task 6 CLI verification |
| Without enable returns `REAL_NETWORK_NOT_ENABLED` | Tasks 1-2; Task 6 |
| Enabled but no implemented client returns `REAL_CLIENT_NOT_IMPLEMENTED_IN_PHASE_3_2C` | Tasks 1-2; Task 6 |
| Enabled dry-run makes zero calls | Tasks 1-2; Task 6 |
| `REAL_REQUEST_HARD_MAX_CODES = 3` and no truncation/reordering | Tasks 1-2; Task 6 |
| `REAL_REQUEST_MAX_DAYS = 31` | Tasks 1-2; Task 6 |
| Injected fake-real only; no HTTP/TCP client | Tasks 2-3; Tasks 5-6 capability scan |
| Dated daily, metadata, benchmark contract | Task 3 fake-real client and audit tests |
| Fund flow disabled/unavailable | Task 1 fake client and Task 4 unavailable tests |
| Cache hit/read failure behavior | Task 3 success, namespace, and malformed-cache refetch tests |
| Partial/all failure behavior | Task 4 |
| Missing safety fields reject downstream buys | Task 4 |
| Benchmark proxy/unavailable behavior | Task 4 |
| Offline `daily_proxy` consumption | Task 5 |
| `REAL_HISTORICAL`/`DAILY_PROXY`/`UNAVAILABLE`/`UNKNOWN`; no `SAMPLE_FIXTURE` field usage | Tasks 3-4 |
| No live/current backfill or automatic execution | Tasks 4-6 scans and documentation |
| No real/example state pollution | Task 5 integration test; Task 6 status inspection |

## Plan Self-Review

- **Scope:** The plan introduces gates, context fields, cache namespace
  separation, audits and tests only. It does not create a concrete
  `AStockRealHistoricalClient` or admit endpoint code.
- **Compatibility:** Existing 2b tests that explicitly inject
  `historical_client` retain the ten-code mocked contract. Only the
  no-injection CLI error is renamed to `REAL_NETWORK_NOT_ENABLED`.
- **No implicit network:** Enabled CLI without an injected fake-real client
  returns `REAL_CLIENT_NOT_IMPLEMENTED_IN_PHASE_3_2C`. Dry-run never calls
  either injection seam.
- **No cache contamination:** Fake-real validation uses
  `fake_real_contract_v1`, distinct from `mock_contract_v1`.
- **Safety and fidelity:** Theme, capital and tail values remain
  `UNAVAILABLE`; unresolved safety fields remain `UNKNOWN`; output remains
  `DAILY_PROXY` and cannot establish profitability.
- **Type consistency:** The planned `real_request_enabled`,
  `effective_real_request_max_codes`, `requested_date_range_days`,
  `max_allowed_days`, and `request_contract` keys are carried consistently
  from request validation through result, manifest, report and tests.
- **Runtime isolation:** The successful fake-real route is test-injected
  only; no CLI fixture loader is introduced, and all generated artifacts
  remain in already ignored cache/processed/manifest/backtest-output paths.

## Execution Handoff

This plan is complete for review. Once approved, execute it with red/green
checkpoints in an isolated implementation run. No implementation step may
send a real request; actual one-code historical endpoint validation remains a
separate, later, explicitly authorized phase.
