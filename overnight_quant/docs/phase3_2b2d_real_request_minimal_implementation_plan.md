# Phase 3.2b-2d Minimal Real Historical Request Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement an explicitly enabled first real historical preparation client for one stock over at most ten natural days, while preserving offline `daily_proxy` backtesting and conservative safety rejection.

**Architecture:** Keep `AStockHistoricalSource` as the normalization boundary and add a focused `AStockRealHistoricalClient` whose only first-run network responsibilities are dated Baidu stock daily K-line retrieval and Eastmoney stable metadata retrieval. Apply an additional `real_first_run` scope gate after the accepted 3-code/31-day gate, let the real client own raw-response caching and request audit, and keep `run_backtest.py` completely offline. Benchmark retrieval is deliberately unavailable in this first implementation so no unverified market series is introduced.

**Tech Stack:** Python standard library only for new network code (`urllib.request`, `urllib.parse`, `json`, `datetime`, `pathlib`, `time`), existing `overnight_quant` CSV/YAML preparation and `daily_proxy` modules, `pytest` with injected fake transports.

---

## Scope Lock

This plan implements only:

```text
prepare_backtest_data.py
  --source a-stock-data
  --enable-real-astock-request
  --codes <exactly one explicit code>
  --start/--end <inclusive range no longer than 10 natural days>
  -> AStockRealHistoricalClient
  -> Baidu dated stock daily K-line + Eastmoney stable metadata
  -> raw-response cache and audit
  -> existing processed CSV/manifest writers
  -> offline daily_proxy loading and conservative rejection
```

It does not implement:

- multiple-stock real requests;
- real requests longer than ten natural days;
- real benchmark retrieval in the first run;
- historical or current fund flow;
- historical or current THS theme attribution;
- Tencent current quote;
- imports from `overnight_quant.data.astock_client`;
- live/demo/positive-fixture fallback;
- networking inside `run_backtest.py` or the backtest engine;
- `strict_historical`;
- automated orders, broker APIs, software clicks, GUI, machine learning, or
  parameter optimization.

## Key Design Decision

The accepted Phase 3.2b-2c gate permits up to three codes and thirty-one
natural days for a future real experiment. Phase 3.2b-2d does not relax or
replace that contract; it narrows the only concrete real implementation:

```python
REAL_REQUEST_HARD_MAX_CODES = 3        # existing outer enabled-request gate
REAL_REQUEST_MAX_DAYS = 31             # existing outer enabled-request gate
REAL_FIRST_RUN_MAX_CODES = 1           # new concrete-client gate
REAL_FIRST_RUN_MAX_DAYS = 10           # new concrete-client gate
```

Validation precedence is:

1. missing codes -> `CODES_REQUIRED`;
2. invalid or reversed date range -> `DATE_RANGE_REQUIRED`;
3. sleep below `0.2` -> `SLEEP_BELOW_MINIMUM`;
4. enabled request exceeding 3 codes -> `MAX_CODES_EXCEEDS_REAL_REQUEST_LIMIT`;
5. enabled request exceeding 31 days -> `DATE_RANGE_EXCEEDS_REAL_REQUEST_LIMIT`;
6. concrete first-run real route exceeding 1 code or 10 days ->
   `REAL_FIRST_RUN_SCOPE_TOO_LARGE`;
7. output directory overwrite validation;
8. non-dry without enable -> `REAL_NETWORK_NOT_ENABLED`;
9. enabled, valid, non-dry first-run CLI may construct the real client.

Thus a four-code request continues to report the accepted outer-gate error,
while a two-code request that fits the outer gate reports the new first-run
error. No failure truncates, reorders, or silently changes the requested
universe.

## First-Run Endpoint Decision

| Requirement | First-Run Data Source | Status | Truth Treatment |
| --- | --- | --- | --- |
| Dated stock daily OHLCV and amount | Baidu `finance.pae.baidu.com/selfselect/getstockquotation` K-line response documented in local `SKILL.md` | Implement | Accepted requested-date rows are `REAL_HISTORICAL` |
| Stable `name` and `list_date` | Eastmoney `push2.eastmoney.com/api/qt/stock/get` fields `f58` and `f189` documented in local `SKILL.md` and existing live client | Implement | Stable metadata with explicit Eastmoney source |
| Dated benchmark K-line | Baidu index variant | Do not implement in first run | Report `BENCHMARK_UNAVAILABLE`; no invented market PASS |
| `limit_up`, `limit_down` | No verified dated contract in first run | Do not populate | `UNKNOWN`; downstream BUY rejected |
| Historical `is_st`, `is_suspended` | No verified dated contract in first run | Do not populate | `UNKNOWN`; downstream BUY rejected |
| `is_bj_stock` | Normalized code-prefix derivation already performed by preparation policy | Retain | `DAILY_PROXY` |
| Theme, capital, intraday tail | No admitted historical source | Do not populate | `UNAVAILABLE` |

The absence of benchmark and safety fields is intentional. A valid first-run
real preparation may produce zero backtest trades; its success criterion is
auditable historical preparation and conservative offline consumption, not a
BUY signal.

## File Map

| Path | Responsibility In This Plan |
| --- | --- |
| `overnight_quant/tests/test_phase32b2d_real_request_minimal.py` | New TDD coverage for first-run limits, fake transport, raw cache, failures, metadata, offline consumption, and prohibited imports |
| `overnight_quant/tests/test_phase32b2c_fake_real_request.py` | Preserve fake-real 3/31 injection contract; retire the now-obsolete no-concrete-client assertion before enabling the CLI real path, and adjust its capability scan |
| `overnight_quant/backtest/astock_real_historical_client.py` | New minimal real client and injectable JSON transport; the only module allowed to import `urllib.request` |
| `overnight_quant/backtest/astock_historical_source.py` | Merge real-client diagnostics into existing normalization/audit contract; retain fake-real behavior |
| `overnight_quant/backtest/data_preparation.py` | Add first-run request context, scope validation, manifest and report disclosure |
| `overnight_quant/scripts/prepare_backtest_data.py` | Construct the real client only after explicit enable and all validation gates; provide test-only transport injection |
| `overnight_quant/config.yaml` | Add first-run real preparation constants only if the implementation elects configuration display; hard limits remain code constants |
| `overnight_quant/README.md` | Document experimental one-code/ten-day command, zero-trade expectation, and no-backtest-network boundary |
| `overnight_quant/backtest_data/README.md` | Document raw cache/audit behavior and ignored outputs |
| `overnight_quant/RELEASE_NOTES.md` | Record minimal real preparation client and its deliberate omissions |

Do not modify `overnight_quant/backtest/backtest_engine.py`, scoring, risk,
fees, live adapters, execution modules, or example/real trading state.

## Runtime Contracts

### New Constants And Error

Add beside the existing real-request gate constants:

```python
REAL_FIRST_RUN_MAX_CODES = 1
REAL_FIRST_RUN_MAX_DAYS = 10
ASTOCK_REAL_ENDPOINT_VERSION = "real_historical_first_run_v1"
```

Add error:

```text
REAL_FIRST_RUN_SCOPE_TOO_LARGE
```

### Request Modes

Keep both existing test modes and add one real mode:

| `request_contract` | Construction | Network Allowed | Limits |
| --- | --- | --- | --- |
| `mocked_contract` | Existing `historical_client=` injected tests | No | Accepted 10-code mock boundary |
| `fake_real_validation` | Existing `real_historical_client=` injected tests | No | Accepted 3-code/31-day gate |
| `real_first_run` | CLI enable flag without an injected historical client, or tests with injected transport | Only preparation client may request | Outer 3/31 plus new 1/10 gate |

### Transport Boundary

`AStockRealHistoricalClient` accepts an internal injected transport for tests.
Production construction uses the standard-library transport:

```python
class JsonTransportProtocol(Protocol):
    def get_text(
        self,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
        timeout: float,
    ) -> str: ...


class UrllibJsonTransport:
    def get_text(self, url, params, headers, timeout):
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(f"{url}?{query}", headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")
```

Tests pass a `FakeJsonTransport` and never instantiate a transport that can
reach a network. The only future manual real request is made after a separate
human approval of the implemented client.

### Raw Cache Ownership

For `request_contract == "real_first_run"`:

- construct `AStockHistoricalSource(..., use_cache=False)` so it does not
  cache normalized records;
- let `AStockRealHistoricalClient` cache each endpoint's raw response text;
- use `build_cache_path()` with endpoint-specific source labels and
  `ASTOCK_REAL_ENDPOINT_VERSION`;
- parse cached raw text through the same JSON/parser path as a fresh response;
- on malformed cache, add `CACHE_READ_FAILED` then perform one enabled
  bounded request;
- on successful transport response, write raw text before parsing so malformed
  external JSON remains auditable;
- never use cache or request failure to switch to sample/demo data.

Endpoint cache source labels:

```text
baidu_daily_kline
eastmoney_stock_metadata
```

Benchmark has no first-run endpoint or cache entry because it is intentionally
unavailable.

### Request Counting And Pacing

The real client records:

```python
network_requests_made: int
cache_hits: int
cache_writes: int
endpoint_attempts: dict[str, int]
endpoint_successes: dict[str, int]
endpoint_failures: dict[str, int]
```

Rules:

- each outbound transport invocation increments `network_requests_made`
  before the call;
- cache hits increment `cache_hits` and do not increment network attempts;
- cache writes increment `cache_writes`;
- the first implementation makes one attempt per enabled cache miss; it does
  not add automatic retries;
- for two cache misses on one stock (daily K-line and metadata), call the
  injected or real sleeper between network invocations using the validated
  `sleep` value;
- benchmark-unavailable does not count as an attempted network request.

No-retry is intentional for this first manual validation: each failure maps
to one auditable attempt, and any retry policy can be introduced in a later
reviewed phase.

## Accepted Real Response Mapping

### Baidu Stock Daily K-Line

Request:

```python
url = "https://finance.pae.baidu.com/selfselect/getstockquotation"
params = {
    "all": "1",
    "isIndex": "false",
    "isBk": "false",
    "isBlock": "false",
    "isFutures": "false",
    "isStock": "true",
    "newFormat": "1",
    "group": "quotation_kline_ab",
    "finClientType": "pc",
    "code": code,
    "start_time": start.replace("-", ""),
    "ktype": "1",
}
```

Parse:

```python
market_data = ((payload.get("Result") or {}).get("newMarketData") or {})
keys = market_data.get("keys") or []
raw_rows = (market_data.get("marketData") or "").split(";")
item = dict(zip(keys, raw_row.split(",")))
```

Output only accepted requested-range dated fields:

```python
{
    "trade_date": _iso_date(item.get("time") or item.get("timestamp")),
    "open": item.get("open", ""),
    "high": item.get("high", ""),
    "low": item.get("low", ""),
    "close": item.get("close", ""),
    "volume": item.get("volume", ""),
    "amount": item.get("amount", ""),
}
```

Do not copy current values, theme, capital, ST, suspension, or price-limit
fields into the historical row. If a valid row date falls outside the
requested range, discard it and record:

```text
HISTORICAL_ROW_OUT_OF_REQUEST_RANGE
```

If the JSON shape cannot yield dated OHLCV/amount rows, record:

```text
BAIDU_DAILY_KLINE_PARSE_FAILED
```

### Eastmoney Stable Metadata

Request:

```python
market_code = "1" if code.startswith(("6", "9")) else "0"
url = "https://push2.eastmoney.com/api/qt/stock/get"
params = {
    "fltt": "2",
    "invt": "2",
    "fields": "f57,f58,f189",
    "secid": f"{market_code}.{code}",
}
```

Output:

```python
raw = payload.get("data") or {}
return {
    "name": str(raw.get("f58") or ""),
    "list_date": _iso_yyyymmdd(raw.get("f189")),
}
```

If metadata retrieval or parsing fails, return `{}` and record its source
error. `AStockHistoricalSource` keeps daily bars with blank `list_date`, so
the downstream safety gate rejects rather than the preparation process
aborting.

### Benchmark And Fund Flow

The first concrete client uses:

```python
def fetch_benchmark_bars(self, symbol: str, start: str, end: str) -> list[dict]:
    self._record_unavailable(symbol, "benchmark", "BENCHMARK_UNAVAILABLE",
                             "not implemented in real first-run client")
    return []


def fetch_fund_flow(self, code: str, start: str, end: str) -> list[dict]:
    return []
```

No benchmark request is issued. The existing preparation path writes stock
processed data and discloses missing market data; offline backtest keeps cash
with `market_data_unavailable`.

## Audit And Truth-Level Contract

For a first-run processed dataset, `dataset_manifest.yaml` includes:

```yaml
source: a-stock-data
fidelity: daily_proxy
selection_as_of: daily_close_proxy
strict_historical_supported: false
real_request_enabled: true
request_contract: real_first_run
real_request_hard_max_codes: 3
max_allowed_days: 31
real_first_run_max_codes: 1
real_first_run_max_days: 10
network_prepared: true
backtest_network_access: false

truth_levels:
  REAL_HISTORICAL: dated Baidu daily rows and disclosed Eastmoney stable metadata
  DAILY_PROXY: calculations derived from accepted historical daily rows
  SAMPLE_FIXTURE: prohibited_for_a_stock_data
  UNAVAILABLE: not reconstructed in this real first-run client
  UNKNOWN: safety value not historically confirmed

warnings:
  - DAILY_PROXY_ONLY
  - NOT_STRICT_HISTORICAL
  - REAL_FIRST_RUN_EXPERIMENTAL_ONE_CODE_TEN_DAYS
  - BENCHMARK_UNAVAILABLE_IN_REAL_FIRST_RUN
  - NO_CURRENT_LIVE_BACKFILL
  - NO_INTRADAY_TAIL_DATA_IF_APPLICABLE
  - NO_HISTORICAL_THEME_IF_APPLICABLE
  - NO_HISTORICAL_FUND_FLOW_IF_APPLICABLE
```

For the first run, coverage is expected to show:

| Fields | Classification |
| --- | --- |
| Accepted OHLCV/amount | `REAL_HISTORICAL` |
| `name`, successful `list_date` | `REAL_HISTORICAL` stable metadata |
| `change_pct`, `ma5`, `ma10`, `ma20`, `vol_ratio`, `range_position`, `is_bj_stock` | `DAILY_PROXY` |
| `tail_pullback_pct`, `theme_tags`, `theme_rank`, `main_net`, `big_order_net` | `UNAVAILABLE` |
| `limit_up`, `limit_down`, `is_st`, `is_suspended`; failed `list_date` | `UNKNOWN` |

`prepare_report_*.md` must add:

```text
request_contract: real_first_run
first_run_scope_limit: 1 code / 10 days
real_first_run_max_codes: 1
real_first_run_max_days: 10
network_requests_made: <integer>
cache_hits: <integer>
cache_writes: <integer>
backtest_network_access: false
benchmark_status: unavailable_in_real_first_run
Research Limitation: DAILY_PROXY only; not strict_historical and not evidence of strategy profitability.
```

`source_errors_*.csv` retains:

```text
code,source,stage,error_code,error_message,retry_count
```

and admits at least:

```text
CACHE_READ_FAILED
HTTP_REQUEST_FAILED
JSON_PARSE_FAILED
BAIDU_DAILY_KLINE_PARSE_FAILED
HISTORICAL_ROW_OUT_OF_REQUEST_RANGE
HISTORICAL_METADATA_FAILED
BENCHMARK_UNAVAILABLE
```

## TDD Task Plan

### Task 1: Lock The Additional First-Run Gate In Tests

**Files:**
- Create: `overnight_quant/tests/test_phase32b2d_real_request_minimal.py`
- Modify: `overnight_quant/backtest/data_preparation.py`
- Modify: `overnight_quant/scripts/prepare_backtest_data.py`
- Modify: `overnight_quant/backtest/astock_historical_source.py`

- [ ] **Step 1: Add failing scope and dry-run tests**

Create `overnight_quant/tests/test_phase32b2d_real_request_minimal.py` with
the initial tests and existing config pattern:

```python
from pathlib import Path

from overnight_quant.scripts.prepare_backtest_data import run_prepare
from overnight_quant.strategy.yang_yongxing_overnight import load_config


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


def test_real_first_run_rejects_two_codes_before_client_construction(tmp_path):
    result = run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        codes=["600519", "300750"],
        start="2025-01-02",
        end="2025-01-10",
        out_dir=str(tmp_path / "processed"),
        max_codes=3,
        sleep=0.5,
        dry_run=True,
        config=_config(tmp_path),
    )

    assert result["error"] == "REAL_FIRST_RUN_SCOPE_TOO_LARGE"
    assert result["network_requests_made"] == 0
    report = Path(result["audit_files"]["prepare_report"]).read_text(encoding="utf-8")
    assert "real_first_run_max_codes: 1" in report


def test_real_first_run_rejects_eleven_days_before_client_construction(tmp_path):
    result = run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        codes=["600519"],
        start="2025-01-01",
        end="2025-01-11",
        out_dir=str(tmp_path / "processed"),
        max_codes=1,
        sleep=0.5,
        dry_run=True,
        config=_config(tmp_path),
    )

    assert result["error"] == "REAL_FIRST_RUN_SCOPE_TOO_LARGE"
    assert result["network_requests_made"] == 0


def test_real_first_run_valid_dry_run_makes_zero_requests(tmp_path):
    result = run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        codes=["600519"],
        start="2025-01-02",
        end="2025-01-10",
        out_dir=str(tmp_path / "processed"),
        max_codes=1,
        sleep=0.5,
        dry_run=True,
        config=_config(tmp_path),
    )

    assert result["status"] == "DRY_RUN"
    assert result["request_contract"] == "real_first_run"
    assert result["network_requests_made"] == 0
```

- [ ] **Step 2: Run tests to prove they fail for the missing narrower gate**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2d_real_request_minimal.py -q
```

Expected before implementation: failures because enabled CLI dry-run still
uses the `fake_real_validation` contract and does not expose
`REAL_FIRST_RUN_SCOPE_TOO_LARGE`.

- [ ] **Step 3: Add constants and request/result context**

In `overnight_quant/backtest/astock_historical_source.py`, add:

```python
REAL_FIRST_RUN_MAX_CODES = 1
REAL_FIRST_RUN_MAX_DAYS = 10
ASTOCK_REAL_ENDPOINT_VERSION = "real_historical_first_run_v1"
```

In `overnight_quant/backtest/data_preparation.py`, import those constants and
extend both `PreparationRequest` and `PreparationResult`:

```python
real_first_run_enabled: bool = False
real_first_run_max_codes: int | None = None
real_first_run_max_days: int | None = None
```

Carry the same fields through dry-run, successful result,
`write_failed_prepare_report()`, `result_to_dict()` output, and
`_empty_astock_audit()`.

- [ ] **Step 4: Make CLI identify the concrete first-run contract**

In `run_prepare()` set:

```python
real_first_run = (
    source == "a-stock-data"
    and enable_real_astock_request
    and real_historical_client is None
)
```

Build `PreparationRequest` with:

```python
real_first_run_enabled=real_first_run,
request_contract=(
    "fake_real_validation"
    if source == "a-stock-data"
       and enable_real_astock_request
       and real_historical_client is not None
    else "real_first_run"
    if real_first_run
    else existing_contract_value
),
```

This preserves the existing injected fake-real 3/31 tests and makes CLI
dry-run exercise the narrower concrete-client scope.

- [ ] **Step 5: Implement nested validation without replacing the outer gate**

In `_validate_request()` retain the current enabled 3/31 checks, then add:

```python
if request.real_first_run_enabled:
    request.real_first_run_max_codes = REAL_FIRST_RUN_MAX_CODES
    request.real_first_run_max_days = REAL_FIRST_RUN_MAX_DAYS
    if (
        len(normalized) > REAL_FIRST_RUN_MAX_CODES
        or request.requested_date_range_days > REAL_FIRST_RUN_MAX_DAYS
    ):
        raise DataPreparationError(
            "REAL_FIRST_RUN_SCOPE_TOO_LARGE",
            (
                f"requested_codes_count={len(normalized)} "
                f"real_first_run_max_codes={REAL_FIRST_RUN_MAX_CODES} "
                f"requested_date_range_days={request.requested_date_range_days} "
                f"real_first_run_max_days={REAL_FIRST_RUN_MAX_DAYS}"
            ),
            source_audit=_empty_astock_audit(request, len(normalized)),
        )
```

Place this after `MAX_CODES_EXCEEDS_REAL_REQUEST_LIMIT` and
`DATE_RANGE_EXCEEDS_REAL_REQUEST_LIMIT` so the outer accepted gate keeps its
public errors.

- [ ] **Step 6: Run gate tests and existing fake-real tests**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2d_real_request_minimal.py overnight_quant/tests/test_phase32b2c_fake_real_request.py -q
```

Expected: first-run dry-run tests pass and the accepted fake-real injected
tests still allow their existing 3/31 contract.

- [ ] **Step 7: Commit the additional gate**

```text
git add -- overnight_quant/tests/test_phase32b2d_real_request_minimal.py overnight_quant/backtest/astock_historical_source.py overnight_quant/backtest/data_preparation.py overnight_quant/scripts/prepare_backtest_data.py
git diff --cached --check
git commit -m "Add first-run real preparation scope gate"
```

### Task 2: Implement A Standard-Library Real Client Behind An Injected Transport

**Files:**
- Create: `overnight_quant/backtest/astock_real_historical_client.py`
- Modify: `overnight_quant/tests/test_phase32b2d_real_request_minimal.py`

- [ ] **Step 1: Add failing transport, parse and cache tests**

Extend the new test file:

```python
import json

from overnight_quant.backtest.astock_real_historical_client import (
    AStockRealHistoricalClient,
)


class FakeJsonTransport:
    def __init__(self, responses=None, failures=None):
        self.responses = responses or {}
        self.failures = failures or {}
        self.calls = []

    def get_text(self, url, params, headers, timeout):
        endpoint = "baidu_daily_kline" if "baidu" in url else "eastmoney_stock_metadata"
        self.calls.append((endpoint, dict(params), timeout))
        if endpoint in self.failures:
            raise self.failures[endpoint]
        return json.dumps(self.responses[endpoint])


def _baidu_payload(include_out_of_range=False):
    rows = [
        "20250102,10.00,10.10,10.20,9.90,100,1010000",
        "20250103,10.10,10.20,10.30,10.00,110,1120000",
    ]
    if include_out_of_range:
        rows.append("20241231,9.50,9.60,9.70,9.40,90,900000")
    return {
        "Result": {
            "newMarketData": {
                "keys": ["time", "open", "close", "high", "low", "volume", "amount"],
                "marketData": ";".join(rows),
            }
        }
    }


def _metadata_payload():
    return {"data": {"f58": "Kweichow Moutai", "f189": "20010827"}}


def test_real_client_parses_only_requested_dated_daily_rows(tmp_path):
    transport = FakeJsonTransport(
        responses={"baidu_daily_kline": _baidu_payload(include_out_of_range=True)}
    )
    client = AStockRealHistoricalClient(
        transport=transport,
        cache_dir=tmp_path / "cache",
        sleep_seconds=0.5,
        sleep_fn=lambda _: None,
    )

    rows = client.fetch_daily_bars("600519", "2025-01-02", "2025-01-10")

    assert [row["trade_date"] for row in rows] == ["2025-01-02", "2025-01-03"]
    errors = client.drain_errors()
    assert any(error.error_code == "HISTORICAL_ROW_OUT_OF_REQUEST_RANGE" for error in errors)


def test_real_client_parses_stable_metadata_only(tmp_path):
    client = AStockRealHistoricalClient(
        transport=FakeJsonTransport(responses={"eastmoney_stock_metadata": _metadata_payload()}),
        cache_dir=tmp_path / "cache",
        sleep_seconds=0.5,
        sleep_fn=lambda _: None,
    )

    metadata = client.fetch_stock_metadata("600519")

    assert metadata == {"name": "Kweichow Moutai", "list_date": "2001-08-27"}


def test_real_client_raw_cache_hit_avoids_transport_call(tmp_path):
    transport = FakeJsonTransport(responses={"baidu_daily_kline": _baidu_payload()})
    client = AStockRealHistoricalClient(
        transport=transport,
        cache_dir=tmp_path / "cache",
        sleep_seconds=0.5,
        sleep_fn=lambda _: None,
    )

    first = client.fetch_daily_bars("600519", "2025-01-02", "2025-01-10")
    calls_after_first = list(transport.calls)
    second = client.fetch_daily_bars("600519", "2025-01-02", "2025-01-10")

    assert second == first
    assert transport.calls == calls_after_first
    assert client.audit_snapshot()["cache_hits"] == 1
    assert client.audit_snapshot()["network_requests_made"] == 1
```

- [ ] **Step 2: Run tests to prove the new client is absent**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2d_real_request_minimal.py -q
```

Expected before implementation: import failure for
`overnight_quant.backtest.astock_real_historical_client`.

- [ ] **Step 3: Create the transport and real-client module**

Create `overnight_quant/backtest/astock_real_historical_client.py` with this
public shape:

```python
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from time import sleep
from typing import Callable, Protocol

from overnight_quant.backtest.astock_historical_source import (
    ASTOCK_REAL_ENDPOINT_VERSION,
    build_cache_path,
)
from overnight_quant.backtest.preparation_sources import SourceError

BAIDU_DAILY_URL = "https://finance.pae.baidu.com/selfselect/getstockquotation"
EASTMONEY_METADATA_URL = "https://push2.eastmoney.com/api/qt/stock/get"
UA = "Mozilla/5.0"


class JsonTransportProtocol(Protocol):
    def get_text(self, url: str, params: dict[str, str],
                 headers: dict[str, str], timeout: float) -> str: ...


class UrllibJsonTransport:
    def get_text(self, url, params, headers, timeout):
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(f"{url}?{query}", headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")


class AStockRealHistoricalClient:
    def __init__(
        self,
        transport: JsonTransportProtocol,
        cache_dir: Path,
        sleep_seconds: float,
        timeout: float = 10.0,
        sleep_fn: Callable[[float], None] = sleep,
    ):
        self.transport = transport
        self.cache_dir = Path(cache_dir)
        self.sleep_seconds = sleep_seconds
        self.timeout = timeout
        self.sleep_fn = sleep_fn
        self._errors: list[SourceError] = []
        self._network_requests_made = 0
        self._cache_hits = 0
        self._cache_writes = 0
        self._has_requested = False
        self._endpoint_attempts: dict[str, int] = {}
        self._endpoint_successes: dict[str, int] = {}
        self._endpoint_failures: dict[str, int] = {}
```

Implement `fetch_daily_bars()`, `fetch_stock_metadata()`,
`fetch_benchmark_bars()`, `fetch_fund_flow()`, `drain_errors()` and
`audit_snapshot()` exactly around the endpoint decisions in this document.
The production constructor must require an explicitly supplied transport; the
CLI task below is the only place that constructs `UrllibJsonTransport`.

- [ ] **Step 4: Implement raw request/cache helper**

In the new class, use a single `_request_json()` method:

```python
def _request_json(self, source, symbol, start, end, url, params, headers):
    cache_path = build_cache_path(
        self.cache_dir,
        source,
        ASTOCK_REAL_ENDPOINT_VERSION,
        symbol,
        start,
        end,
        params,
    )
    if cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            self._cache_hits += 1
            return payload
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            self._add_error(symbol, "cache_read", "CACHE_READ_FAILED", str(exc))
    if self._has_requested:
        self.sleep_fn(self.sleep_seconds)
    self._has_requested = True
    self._network_requests_made += 1
    self._increment(self._endpoint_attempts, source)
    try:
        raw_text = self.transport.get_text(url, params, headers, self.timeout)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        self._increment(self._endpoint_failures, source)
        self._add_error(symbol, source, "HTTP_REQUEST_FAILED", str(exc))
        return {}
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(raw_text, encoding="utf-8")
        self._cache_writes += 1
    except OSError as exc:
        self._add_error(symbol, "cache_write", "CACHE_WRITE_FAILED", str(exc))
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        self._increment(self._endpoint_failures, source)
        self._add_error(symbol, source, "JSON_PARSE_FAILED", str(exc))
        return {}
    self._increment(self._endpoint_successes, source)
    return payload
```

Do not implement retries or alternate sources in this first client.

- [ ] **Step 5: Implement admitted parsers and unavailable methods**

Use helper functions to normalize `YYYYMMDD` or `YYYY-MM-DD` into ISO dates,
accept only requested-range daily rows, and append
`HISTORICAL_ROW_OUT_OF_REQUEST_RANGE` for dropped valid dated rows.

`fetch_benchmark_bars()` must not call `_request_json()`:

```python
def fetch_benchmark_bars(self, symbol, start, end):
    self._add_error(
        symbol,
        "benchmark",
        "BENCHMARK_UNAVAILABLE",
        "not implemented in real first-run client",
    )
    return []


def fetch_fund_flow(self, code, start, end):
    return []
```

- [ ] **Step 6: Run client unit tests**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2d_real_request_minimal.py -q
```

Expected: parser and cache tests pass through injected `FakeJsonTransport`;
there are no live endpoint calls.

- [ ] **Step 7: Commit the isolated client**

```text
git add -- overnight_quant/backtest/astock_real_historical_client.py overnight_quant/tests/test_phase32b2d_real_request_minimal.py
git diff --cached --check
git commit -m "Add minimal real historical client transport contract"
```

### Task 3: Merge Real Client Diagnostics Into Processed Preparation

**Files:**
- Modify: `overnight_quant/backtest/astock_historical_source.py`
- Modify: `overnight_quant/scripts/prepare_backtest_data.py`
- Modify: `overnight_quant/tests/test_phase32b2c_fake_real_request.py`
- Modify: `overnight_quant/tests/test_phase32b2d_real_request_minimal.py`

- [ ] **Step 1: Retire the obsolete 2c no-client execution assertion before activating the real route**

Delete this Phase 3.2b-2c test before `run_prepare()` can construct a real
client:

```python
def test_enabled_non_dry_without_real_client_returns_not_implemented(...):
    ...
    assert result["error"] == "REAL_CLIENT_NOT_IMPLEMENTED_IN_PHASE_3_2C"
```

That result is correct only while no real client exists. Keeping the test
after Task 3 would cause the full suite to attempt the newly admitted CLI
path without a fake transport. Its replacement is the new first-run
integration test below, which always passes an injected transport and proves
the same orchestration boundary without outbound I/O.

- [ ] **Step 2: Add failing integration tests using fake transport**

Extend tests with a `run_prepare()` helper that supplies a fake transport,
not an injected fake historical row client:

```python
def _run_real_first_prepare(tmp_path, transport):
    return run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        real_transport=transport,
        codes=["600519"],
        start="2025-01-02",
        end="2025-01-10",
        out_dir=str(tmp_path / "processed"),
        max_codes=1,
        sleep=0.5,
        overwrite=True,
        config=_config(tmp_path),
    )


def test_real_first_run_fake_transport_writes_processed_and_audit(tmp_path):
    transport = FakeJsonTransport(
        responses={
            "baidu_daily_kline": _baidu_payload(),
            "eastmoney_stock_metadata": _metadata_payload(),
        }
    )

    result = _run_real_first_prepare(tmp_path, transport)

    assert result["status"] == "PARTIAL_DATA_PREPARED"
    assert result["network_requests_made"] == 2
    manifest = Path(result["out_dir"], "dataset_manifest.yaml").read_text(encoding="utf-8")
    report = Path(result["audit_files"]["prepare_report"]).read_text(encoding="utf-8")
    assert "request_contract: real_first_run" in manifest
    assert "network_prepared: true" in manifest
    assert "real_first_run_max_codes: 1" in report
    assert "real_first_run_max_days: 10" in report
    assert "backtest_network_access: false" in report
    assert "BENCHMARK_UNAVAILABLE" in Path(result["audit_files"]["source_errors"]).read_text(encoding="utf-8")


def test_real_first_run_metadata_failure_keeps_bars_and_blank_list_date(tmp_path):
    transport = FakeJsonTransport(
        responses={"baidu_daily_kline": _baidu_payload()},
        failures={"eastmoney_stock_metadata": OSError("metadata offline")},
    )

    result = _run_real_first_prepare(tmp_path, transport)

    rows = Path(result["out_dir"], "daily_bars.csv").read_text(encoding="utf-8")
    assert "2001-08-27" not in rows
    coverage = Path(result["audit_files"]["field_coverage"]).read_text(encoding="utf-8")
    assert "daily_bars,list_date,0,2,0.0,UNKNOWN" in coverage
```

- [ ] **Step 3: Run integration tests to prove orchestration is not connected**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2d_real_request_minimal.py -q
```

Expected before implementation: failure because `run_prepare()` has no
`real_transport` parameter and enabled CLI still reports that a concrete
client is not implemented.

- [ ] **Step 4: Add diagnostics merging in the source boundary**

In `AStockHistoricalSource`, add a private merge after each real-client method
call and before deciding whether a generic missing-row error is needed:

```python
def _merge_client_diagnostics(self, errors: list[SourceError], audit: SourceAudit) -> None:
    drain = getattr(self.client, "drain_errors", None)
    if callable(drain):
        errors.extend(drain())
    snapshot = getattr(self.client, "audit_snapshot", None)
    if callable(snapshot):
        values = snapshot()
        audit.network_requests_made = int(values.get("network_requests_made", 0))
        audit.cache_hits = int(values.get("cache_hits", 0))
        audit.cache_writes = int(values.get("cache_writes", 0))
        audit.endpoint_attempts = dict(values.get("endpoint_attempts", {}))
        audit.endpoint_successes = dict(values.get("endpoint_successes", {}))
        audit.endpoint_failures = dict(values.get("endpoint_failures", {}))
```

Extend `SourceAudit` with the endpoint-count dictionaries. Call this method
immediately after daily, metadata, and benchmark fetches. Ensure a client
error with stage `daily_bars_primary` prevents a redundant generic daily-bar
failure row.

Also change `_cached_or_fetch()` so `audit.mock_client_calls` is incremented
only when `request_contract != "real_first_run"`. For the real route,
`network_requests_made` and endpoint counters from the concrete client are
the truthful measurements; a report must not describe real attempts as mock
calls.

- [ ] **Step 5: Construct the concrete client only after the gates**

In `run_prepare()`, add the test-only internal argument:

```python
real_transport=None
```

For `request_contract == "real_first_run"` and non-dry execution, create:

```python
transport = real_transport or UrllibJsonTransport()
real_client = AStockRealHistoricalClient(
    transport=transport,
    cache_dir=request.cache_dir,
    sleep_seconds=request.sleep,
)
source_factory = lambda validated: AStockHistoricalSource(
    real_client,
    validated.cache_dir,
    effective_max_codes=REAL_FIRST_RUN_MAX_CODES,
    endpoint_version=ASTOCK_REAL_ENDPOINT_VERSION,
    request_contract="real_first_run",
    real_request_enabled=True,
    effective_real_request_max_codes=validated.effective_real_request_max_codes,
    requested_date_range_days=validated.requested_date_range_days,
    max_allowed_days=validated.max_allowed_days,
    use_cache=False,
)
```

Do not construct `UrllibJsonTransport` for dry-run, for an unenabled request,
or for a rejected request.

- [ ] **Step 6: Keep earlier fake-real behavior isolated**

Do not change the existing branch:

```python
enable_real_astock_request=True and real_historical_client is not None
```

It continues using `fake_real_validation` and
`ASTOCK_FAKE_REAL_ENDPOINT_VERSION`, so prior cache and 3/31 tests remain
deterministic and network-free.

- [ ] **Step 7: Run focused preparation tests**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2d_real_request_minimal.py overnight_quant/tests/test_phase32b2c_fake_real_request.py overnight_quant/tests/test_phase32b2_astock_mocked_source.py -q
```

Expected: all integration and backward-compatibility tests pass without
using a production transport.

- [ ] **Step 8: Commit the preparation integration**

```text
git add -- overnight_quant/backtest/astock_historical_source.py overnight_quant/scripts/prepare_backtest_data.py overnight_quant/tests/test_phase32b2c_fake_real_request.py overnight_quant/tests/test_phase32b2d_real_request_minimal.py
git diff --cached --check
git commit -m "Connect bounded real historical preparation client"
```

### Task 4: Record Truth Levels, Failure Audits, And Conservative Offline Consumption

**Files:**
- Modify: `overnight_quant/backtest/data_preparation.py`
- Modify: `overnight_quant/tests/test_phase32b2d_real_request_minimal.py`

- [ ] **Step 1: Add failing audit, failure and offline-consumption tests**

Add tests:

```python
from overnight_quant.scripts.run_backtest import run_backtest


def test_real_first_run_malformed_cache_records_failure_then_refetches(tmp_path):
    transport = FakeJsonTransport(
        responses={
            "baidu_daily_kline": _baidu_payload(),
            "eastmoney_stock_metadata": _metadata_payload(),
        }
    )
    # Seed the Baidu raw-cache path returned by build_cache_path with invalid JSON.
    invalid_path = _baidu_cache_path(tmp_path)
    invalid_path.parent.mkdir(parents=True, exist_ok=True)
    invalid_path.write_text("{broken-json", encoding="utf-8")

    result = _run_real_first_prepare(tmp_path, transport)

    errors = Path(result["audit_files"]["source_errors"]).read_text(encoding="utf-8")
    assert "CACHE_READ_FAILED" in errors
    assert any(call[0] == "baidu_daily_kline" for call in transport.calls)


def test_real_first_run_http_and_json_failures_are_audited(tmp_path):
    http_result = _run_real_first_prepare(
        tmp_path / "http",
        FakeJsonTransport(failures={"baidu_daily_kline": OSError("HTTP 503")}),
    )
    assert http_result["error"] == "NO_DAILY_BARS_FETCHED"
    assert "HTTP_REQUEST_FAILED" in Path(http_result["audit_files"]["source_errors"]).read_text(encoding="utf-8")

    json_result = _run_real_first_prepare(
        tmp_path / "json",
        FakeJsonTransport(
            responses={"eastmoney_stock_metadata": _metadata_payload()},
            raw_overrides={"baidu_daily_kline": "{not-json"},
        ),
    )
    assert json_result["error"] == "NO_DAILY_BARS_FETCHED"
    assert "JSON_PARSE_FAILED" in Path(json_result["audit_files"]["source_errors"]).read_text(encoding="utf-8")


def test_real_first_run_prepared_data_is_consumed_offline_and_rejects_buy(tmp_path):
    config = _config(tmp_path)
    transport = FakeJsonTransport(
        responses={
            "baidu_daily_kline": _baidu_payload(),
            "eastmoney_stock_metadata": _metadata_payload(),
        }
    )
    prepared = _run_real_first_prepare(tmp_path, transport)
    calls_after_prepare = list(transport.calls)

    result = run_backtest(
        dataset="local",
        fidelity="daily_proxy",
        data_dir=prepared["out_dir"],
        run_id="real-first-run-offline",
        config=config,
    )

    assert "error" not in result
    assert transport.calls == calls_after_prepare
    rejection = Path(result["output_dir"], "rejections.csv").read_text(encoding="utf-8")
    skipped = Path(result["output_dir"], "skipped_days.csv").read_text(encoding="utf-8")
    assert "limit_price_unknown" in rejection or "market_data_unavailable" in skipped
    assert "Report Fidelity: DAILY_PROXY" in Path(result["output_dir"], "backtest_summary.md").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run new tests to identify missing report fields**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2d_real_request_minimal.py -q
```

Expected before report changes: preparation succeeds through fake transport
but manifest/report assertions for real-first-run fields fail.

- [ ] **Step 3: Extend manifest/report generation for real first-run**

In `_build_manifest()` branch on:

```python
real_first_run = request.request_contract == "real_first_run"
```

For this branch set:

```python
"real_first_run_max_codes": REAL_FIRST_RUN_MAX_CODES,
"real_first_run_max_days": REAL_FIRST_RUN_MAX_DAYS,
"network_prepared": True,
"fake_real_client_prepared": False,
"backtest_network_access": False,
```

Change truth text only for this branch:

```python
"REAL_HISTORICAL": "dated Baidu daily rows and disclosed Eastmoney stable metadata",
"UNAVAILABLE": "not reconstructed in real first-run client",
```

Append the real-first-run warnings listed in the audit contract. Do not remove
the existing `DAILY_PROXY_ONLY`, `NOT_STRICT_HISTORICAL`, or
`SAMPLE_FIXTURE: prohibited_for_a_stock_data` declaration.

- [ ] **Step 4: Add report source statistics and scope display**

In both `write_failed_prepare_report()` and `_write_audit_files()` include:

```python
f"first_run_scope_limit: {REAL_FIRST_RUN_MAX_CODES} code / {REAL_FIRST_RUN_MAX_DAYS} days",
f"real_first_run_max_codes: {request.real_first_run_max_codes or 'not_applicable'}",
f"real_first_run_max_days: {request.real_first_run_max_days or 'not_applicable'}",
f"backtest_network_access: false",
f"endpoint_attempts: {manifest.get('endpoint_attempts', {})}",
f"endpoint_successes: {manifest.get('endpoint_successes', {})}",
f"endpoint_failures: {manifest.get('endpoint_failures', {})}",
```

For successfully prepared real-first-run data also print:

```text
benchmark_status: unavailable_in_real_first_run
Research Limitation: DAILY_PROXY only; not strict_historical and not evidence of strategy profitability.
```

- [ ] **Step 5: Run audit/offline tests plus all historical preparation tests**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2d_real_request_minimal.py overnight_quant/tests/test_phase32b2c_fake_real_request.py overnight_quant/tests/test_phase32b2_astock_mocked_source.py overnight_quant/tests/test_phase32b_preparation.py -q
```

Expected: all tests pass; the backtest test performs no transport call after
processed files have been written.

- [ ] **Step 6: Commit audit and safety disclosure**

```text
git add -- overnight_quant/backtest/data_preparation.py overnight_quant/tests/test_phase32b2d_real_request_minimal.py
git diff --cached --check
git commit -m "Audit minimal real historical preparation results"
```

### Task 5: Update Capability Guards And Operator Documentation

**Files:**
- Modify: `overnight_quant/tests/test_phase32b2c_fake_real_request.py`
- Modify: `overnight_quant/tests/test_phase32b2d_real_request_minimal.py`
- Modify: `overnight_quant/README.md`
- Modify: `overnight_quant/backtest_data/README.md`
- Modify: `overnight_quant/RELEASE_NOTES.md`

- [ ] **Step 1: Replace the prior no-network scan with an allowlisted boundary test**

Phase 3.2b-2c intentionally prohibited `urllib.request` everywhere because a
real client did not exist. In 2d, update that assertion without weakening the
prohibition against live/current/automatic behavior:

```python
def test_real_client_is_the_only_preparation_network_module():
    root = Path(__file__).resolve().parents[1]
    real_client = root / "backtest" / "astock_real_historical_client.py"
    non_network_paths = [
        root / "backtest" / "astock_historical_source.py",
        root / "backtest" / "data_preparation.py",
        root / "scripts" / "prepare_backtest_data.py",
        root / "backtest" / "backtest_engine.py",
        root / "scripts" / "run_backtest.py",
    ]
    assert "urllib.request" in real_client.read_text(encoding="utf-8")
    text = "\n".join(path.read_text(encoding="utf-8").lower() for path in non_network_paths)
    assert "urllib.request" not in text
    all_production = text + real_client.read_text(encoding="utf-8").lower()
    forbidden = [
        "overnight_quant.data.astock_client",
        "qt.gtimg.cn",
        "10jqka",
        "/fflow/",
        "mootdx",
        "pyautogui",
        "selenium",
        "broker api",
        "auto" + "_order",
        "place" + "_order",
    ]
    assert not any(token in all_production for token in forbidden)
```

Adjust the older fake-real prohibition test to continue scanning its three
non-network production paths only; it must not treat the new dedicated client
as part of the no-network 2c assertion.

- [ ] **Step 2: Add test that CLI dry-run never constructs the production transport**

Monkeypatch the new transport constructor in the script module to raise if it
is instantiated, then execute a valid enabled dry-run:

```python
def test_real_first_run_dry_run_never_constructs_transport(tmp_path, monkeypatch):
    def forbidden_transport():
        raise AssertionError("dry-run must not construct transport")

    monkeypatch.setattr(
        "overnight_quant.scripts.prepare_backtest_data.UrllibJsonTransport",
        forbidden_transport,
    )
    result = run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        codes=["600519"],
        start="2025-01-02",
        end="2025-01-10",
        out_dir=str(tmp_path / "processed"),
        max_codes=1,
        sleep=0.5,
        dry_run=True,
        config=_config(tmp_path),
    )
    assert result["status"] == "DRY_RUN"
    assert result["network_requests_made"] == 0
```

- [ ] **Step 3: Document the experimental real preparation boundary**

Update the README files with:

```text
Phase 3.2b-2d permits an explicitly enabled experimental real preparation
request for exactly one supplied code over no more than ten natural days.
It fetches dated stock daily bars and stable listing metadata only. It does
not fetch benchmark, theme, capital flow, live quote, or historical safety
confirmation in the first run. Resulting datasets remain DAILY_PROXY only,
normally retain safety/market rejections, and cannot establish profitability.
run_backtest.py remains offline.
```

Document the manual command as **planned verification only, not part of
automated tests**:

```text
python overnight_quant/scripts/prepare_backtest_data.py \
  --source a-stock-data \
  --enable-real-astock-request \
  --codes 600519 \
  --start 2025-01-02 \
  --end 2025-01-10 \
  --out-dir overnight_quant/backtest_data/processed \
  --max-codes 1 \
  --sleep 0.5 \
  --overwrite

python overnight_quant/scripts/run_backtest.py \
  --dataset local \
  --fidelity daily_proxy \
  --data-dir overnight_quant/backtest_data/processed
```

Add release notes saying the client is limited to Baidu dated stock K-lines
and Eastmoney stable metadata; benchmark/fund/theme/safety enrichment remain
disabled.

- [ ] **Step 4: Run focused tests for network isolation and docs-adjacent behavior**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2d_real_request_minimal.py overnight_quant/tests/test_phase32b2c_fake_real_request.py -q
```

Expected: all pass using fake transport only; no automated test sends a real
request.

- [ ] **Step 5: Commit documentation and capability guards**

```text
git add -- overnight_quant/tests/test_phase32b2c_fake_real_request.py overnight_quant/tests/test_phase32b2d_real_request_minimal.py overnight_quant/README.md overnight_quant/backtest_data/README.md overnight_quant/RELEASE_NOTES.md
git diff --cached --check
git commit -m "Document minimal real historical preparation boundary"
```

### Task 6: Verification Before Any Manual Real Request

**Files:**
- No additional implementation files. If verification reveals a defect,
  first add a failing focused test in
  `overnight_quant/tests/test_phase32b2d_real_request_minimal.py`, then patch
  only the implicated file.

- [ ] **Step 1: Run the complete automated suite**

Run:

```text
python -m pytest overnight_quant/tests -q
```

Expected: the complete accepted suite plus the new 2d tests pass. Every 2d
network-oriented test uses `FakeJsonTransport`.

- [ ] **Step 2: Verify first-run dry-run is still zero-network**

Run:

```text
python overnight_quant/scripts/prepare_backtest_data.py \
  --source a-stock-data \
  --enable-real-astock-request \
  --codes 600519 \
  --start 2025-01-02 \
  --end 2025-01-10 \
  --out-dir overnight_quant/backtest_data/processed/real_first_run_dry_probe \
  --max-codes 1 \
  --sleep 0.5 \
  --dry-run
```

Expected:

```text
DRY_RUN
real_request_enabled: true
network_requests_made: 0
Codes: 600519
Date Range: 2025-01-02 to 2025-01-10
```

- [ ] **Step 3: Verify narrower first-run limits without source access**

Two-code probe:

```text
python overnight_quant/scripts/prepare_backtest_data.py \
  --source a-stock-data \
  --enable-real-astock-request \
  --codes 600519,300750 \
  --start 2025-01-02 \
  --end 2025-01-10 \
  --out-dir overnight_quant/backtest_data/processed/real_first_run_code_probe \
  --max-codes 3 \
  --sleep 0.5 \
  --dry-run
```

Eleven-day probe:

```text
python overnight_quant/scripts/prepare_backtest_data.py \
  --source a-stock-data \
  --enable-real-astock-request \
  --codes 600519 \
  --start 2025-01-01 \
  --end 2025-01-11 \
  --out-dir overnight_quant/backtest_data/processed/real_first_run_date_probe \
  --max-codes 1 \
  --sleep 0.5 \
  --dry-run
```

Expected for both:

```text
REAL_FIRST_RUN_SCOPE_TOO_LARGE
network_requests_made: 0
```

- [ ] **Step 4: Verify outer accepted gates still take precedence**

Run the prior four-code and thirty-two-day dry-run probes. Expected errors
remain:

```text
MAX_CODES_EXCEEDS_REAL_REQUEST_LIMIT
DATE_RANGE_EXCEEDS_REAL_REQUEST_LIMIT
```

and both report `network_requests_made: 0`.

- [ ] **Step 5: Verify no forbidden imports or state pollution**

Run:

```text
rg -n -i "overnight_quant\.data\.astock_client|qt\.gtimg\.cn|10jqka|/fflow/|mootdx|pyautogui|selenium|broker api|auto_order|place_order" overnight_quant/backtest/astock_real_historical_client.py overnight_quant/backtest/astock_historical_source.py overnight_quant/backtest/data_preparation.py overnight_quant/scripts/prepare_backtest_data.py overnight_quant/backtest/backtest_engine.py overnight_quant/scripts/run_backtest.py
git status --short --ignored --untracked-files=all
git diff --check
```

Expected:

- the dedicated real historical client contains only the approved Baidu
  historical and Eastmoney metadata endpoints;
- no live adapter, current quote, fund-flow, automation, or broker path is
  introduced;
- any cache, processed, manifest or backtest output generated by probes is
  ignored;
- task-external untracked files remain unstaged.

- [ ] **Step 6: Stop before performing the real manual validation command**

Do **not** run the non-dry command that constructs `UrllibJsonTransport` in
this implementation-verification session. Report that implementation and
fake-transport verification are complete, then obtain explicit user approval
for the one-stock real request.

After that separate approval, the allowed manual probe is exactly:

```text
python overnight_quant/scripts/prepare_backtest_data.py \
  --source a-stock-data \
  --enable-real-astock-request \
  --codes 600519 \
  --start 2025-01-02 \
  --end 2025-01-10 \
  --out-dir overnight_quant/backtest_data/processed \
  --max-codes 1 \
  --sleep 0.5 \
  --overwrite

python overnight_quant/scripts/run_backtest.py \
  --dataset local \
  --fidelity daily_proxy \
  --data-dir overnight_quant/backtest_data/processed
```

Even after a successful manual probe, the report language remains:

```text
DAILY_PROXY only
NOT_STRICT_HISTORICAL
not evidence of strategy profitability
```

## Requirements Traceability

| Confirmed Requirement | Plan Coverage |
| --- | --- |
| Existing enable flag remains mandatory | Tasks 1 and 3; Task 6 dry-run |
| Existing 3-code/31-day gate remains intact | Task 1 nested validation; Task 6 outer-gate probes |
| Concrete real first run is limited to 1 code/10 days | Task 1; Task 6 narrower probes |
| Extra error `REAL_FIRST_RUN_SCOPE_TOO_LARGE` | Task 1 |
| Baidu dated stock daily K-line only | Task 2 endpoint/parser |
| Eastmoney stable `name`/`list_date` only | Task 2 endpoint/parser |
| Benchmark may remain unavailable first run | Key decision; Tasks 2-4 |
| No fund flow, THS theme, Tencent quote, live adapter, or fallback | Scope lock; Task 5 capability guard; Task 6 scan |
| Raw-response cache before network | Task 2 raw cache tests |
| Cache hit avoids transport; malformed cache refetches and audits | Tasks 2 and 4 |
| Timeout, user-agent, HTTP/JSON errors audited | Task 2 transport; Task 4 failure tests |
| Out-of-request-range dated rows are dropped and audited | Task 2 parser test |
| Metadata failure does not block prepared bars, but leaves safety rejection | Task 3 |
| Unknown safety fields prevent BUY | Audit contract; Task 4 offline test |
| All network is in preparation, none in backtest | Tasks 3-6 |
| Outputs are ignored and do not touch real/example trading state | Tasks 4 and 6 |
| Reports remain `DAILY_PROXY` / not strict historical | Audit contract; Tasks 4-6 |
| No automatic order or click code | Scope lock; Tasks 5-6 |
| Obsolete no-client test cannot accidentally initiate the real path | Task 3 before real client construction |

## Plan Self-Review

- **Scope:** One new focused client module, nested first-run validation,
  diagnostic plumbing, tests and documentation only. There is no strategy,
  scoring, risk, execution or backtest-engine expansion.
- **Conservatism:** Benchmark is intentionally unavailable in the first
  implementation. Safety fields stay unknown unless a dated admitted source
  proves them; producing no trades is acceptable.
- **Network boundary:** `urllib.request` is allowed only inside
  `astock_real_historical_client.py`. Automated tests inject a fake transport;
  the non-dry manual real command is explicitly deferred until separate user
  approval.
- **Existing contracts:** `mocked_contract` and `fake_real_validation` keep
  their accepted limits and no-network behavior. `real_first_run` applies the
  additional 1-code/10-day restriction after the existing enabled gate. The
  former 2c assertion that a real client is unimplemented is removed before
  enabling its replacement, so automated tests do not accidentally issue a
  concrete request.
- **Raw caching:** The concrete client, not the existing normalized source
  cache, owns raw endpoint responses on the real-first-run path; this meets
  replay/audit requirements without altering fake-real fixtures.
- **Truthful counters:** `mock_client_calls` is not incremented on
  `real_first_run`; request counts for that route come only from the concrete
  client's network/cache/endpoint audit.
- **No future fill:** The plan does not admit current quote, current theme,
  current capital flow, demo data or positive fixture values into historical
  rows.
- **Fidelity language:** Every prepared real-first-run output remains
  `DAILY_PROXY only`, `NOT_STRICT_HISTORICAL`, and not evidence of strategy
  profitability.
- **No placeholders:** Endpoint choices, unimplemented benchmark behavior,
  constants, errors, test seams, commands and expected outcomes are explicit.
