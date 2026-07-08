# Phase 3.2b-2e Real Cache Replay Reproducibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove that the explicitly enabled one-code/ten-day real historical preparation path is replayable from raw cache with zero repeat network requests, stable processed research inputs, and complete cache/audit disclosure.

**Architecture:** Keep the Phase 3.2b-2d endpoint boundary unchanged: only the preparation client may access Baidu dated daily K-line and Eastmoney stable metadata. Extend the existing raw-cache audit surface with `cache_enabled` and `cache_read_failures`, expose replay counters in CLI/report output, and verify reproducibility using deterministic fake-transport tests plus one warm-cache manual replay of the already authorized `600519` request. Backtesting remains an offline consumer of processed CSV files and remains labeled `DAILY_PROXY`.

**Tech Stack:** Python standard library (`json`, `hashlib`, `pathlib`), existing `overnight_quant` preparation/backtest modules, `pytest`, Git-ignored raw cache and generated reports.

---

## Scope Lock

This phase may implement only:

```text
real_first_run raw-cache replay audit
  -> cache_enabled/cache_hits/cache_writes/cache_read_failures/network_requests_made
  -> replay-safe processed-output comparison
  -> offline daily_proxy consumption proof
```

It must not implement or enable:

- any real request above one explicit code or ten natural days;
- a new historical endpoint, benchmark retrieval, fund flow, THS themes, or Tencent current quote;
- any live adapter import, sample/demo fallback, or positive fixture fallback on the real path;
- networking during `run_backtest.py`;
- automated orders, software clicking, broker APIs, GUI, machine learning, or parameter optimization.

The existing gates remain unchanged:

```python
REAL_REQUEST_HARD_MAX_CODES = 3
REAL_REQUEST_MAX_DAYS = 31
REAL_FIRST_RUN_MAX_CODES = 1
REAL_FIRST_RUN_MAX_DAYS = 10
```

## Important Acceptance Interpretation

The accepted Phase 3.2b-2d manual validation has already executed the exact
allowed request for `600519` from `2025-01-02` through `2025-01-10`, producing
ignored raw cache files and this cold-fill evidence:

```text
network_requests_made: 2
cache_writes: 2
endpoint_successes: baidu_daily_kline=1, eastmoney_stock_metadata=1
```

Therefore Phase 3.2b-2e must not delete or corrupt that real cache merely to
force a second outbound request. Its manual real-data acceptance uses the
existing cache as the prepared baseline:

1. Retain the Phase 3.2b-2d cold-fill report as evidence that real responses
   were written once.
2. Run the identical enabled command once as a warm replay.
3. Require `network_requests_made: 0`, `cache_hits >= 2`, and
   `cache_writes: 0` on that replay.
4. Verify malformed-cache recovery only with temporary test cache directories
   and injected fake transports.

An independently repeated cold-fill network fetch would require an explicit
future authorization to remove only the two ignored raw entries first; it is
not part of this phase.

## File Map

| Path | Responsibility In This Plan |
| --- | --- |
| `overnight_quant/tests/test_phase32b2d_real_request_minimal.py` | Extend real-first-run tests for replay, cache-read failure counting, processed consistency, and offline consumption |
| `overnight_quant/backtest/astock_real_historical_client.py` | Publish raw-cache enabled/read-failure diagnostics without changing admitted endpoints |
| `overnight_quant/backtest/astock_historical_source.py` | Carry new cache diagnostics through the normalized source audit contract |
| `overnight_quant/backtest/data_preparation.py` | Add cache replay fields to manifest and preparation reports, including failure reports |
| `overnight_quant/scripts/prepare_backtest_data.py` | Print cache replay counters for `--source a-stock-data` CLI verification |
| `overnight_quant/README.md` | Document warm-cache replay workflow and the existing cold-fill evidence requirement |
| `overnight_quant/backtest_data/README.md` | Document ignored raw-cache replay behavior and non-destructive malformed-cache testing |
| `overnight_quant/RELEASE_NOTES.md` | Record Phase 3.2b-2e replay/audit hardening |

Do not modify the backtest engine, scoring, risk gates, fees, live adapters,
execution modules, or real/example trading-state paths.

## Replay Contract

For `request_contract: real_first_run`, reports and CLI output must contain:

```text
cache_enabled: true
cache_hits: <integer>
cache_writes: <integer>
cache_read_failures: <integer>
network_requests_made: <integer>
backtest_network_access: false
real_request_enabled: true
request_contract: real_first_run
```

Expected counter profiles:

| Situation | `network_requests_made` | `cache_hits` | `cache_writes` | `cache_read_failures` |
| --- | ---: | ---: | ---: | ---: |
| Cold fake-transport preparation with no cache | 2 | 0 | 2 | 0 |
| Replay with both cached raw responses present | 0 | at least 2 | 0 | 0 |
| One malformed cached endpoint and one valid cached endpoint | 1 | 1 | 1 | 1 |

`BENCHMARK_UNAVAILABLE` remains a deliberate capability disclosure; it does
not trigger a request and does not alter the cache replay expectations.

## Source Error Audit Contract

The real client may retain low-level diagnostics such as
`HTTP_REQUEST_FAILED` and `JSON_PARSE_FAILED`, but the preparation audit must
also provide the stable stage-level codes required for downstream review:

| Failure | Required Stable Audit Code | May Also Include |
| --- | --- | --- |
| Malformed raw cache followed by re-fetch | `CACHE_READ_FAILED` | Transport/parse result of the re-fetch |
| No accepted stock daily bars after source attempt | `HISTORICAL_DAILY_BAR_SOURCE_FAILED` | `HTTP_REQUEST_FAILED`, `JSON_PARSE_FAILED`, or range-discard audit |
| Stable metadata cannot supply a usable metadata response | `HISTORICAL_METADATA_FAILED` | `HTTP_REQUEST_FAILED` or `JSON_PARSE_FAILED` |
| Deliberately unsupported first-run benchmark | `BENCHMARK_UNAVAILABLE` | None required |

This is an audit clarification, not a data fallback: missing daily bars still
fail preparation, and missing metadata still leaves `list_date` unknown for
conservative downstream rejection.

## Processed Output Consistency Rule

Replaying raw responses must reproduce the same processed research inputs. Use
two comparison levels:

1. `daily_bars.csv`, `selection_snapshots.csv`, `market_snapshots.csv`, and
   `benchmark_bars.csv` must be byte-identical between cold fixture
   preparation and cache replay.
2. `dataset_manifest.yaml` must be equal after removing run/audit-volatile
   keys:

```python
VOLATILE_MANIFEST_KEYS = {
    "created_at",
    "cache_enabled",
    "network_requests_made",
    "cache_hits",
    "cache_writes",
    "cache_read_failures",
    "endpoint_attempts",
    "endpoint_successes",
    "endpoint_failures",
}
```

The manifest intentionally differs in those audit fields because the accepted
Phase 3.2b-2d cold-fill predates the explicit `cache_enabled` field and the
first/new runs expose different cache counters. Those differences are evidence
of replay/audit behavior, not a processed-data inconsistency.

## Task 1: Lock Replay And Audit Requirements With Failing Tests

**Files:**
- Modify: `overnight_quant/tests/test_phase32b2d_real_request_minimal.py`

- [ ] **Step 1: Add a replay test that uses a new transport instance on the second preparation**

Append helpers and a test that share the same temporary cache directory but
would fail if the replay tried to call transport:

```python
class FailIfCalledTransport:
    def __init__(self):
        self.calls = []

    def get_text(self, url, params, headers, timeout):
        self.calls.append((url, dict(params), timeout))
        raise AssertionError("cache replay must not call transport")


def test_real_first_run_replay_uses_raw_cache_without_transport_calls(tmp_path):
    first = _run_real_first_prepare(tmp_path, _successful_transport())
    second_transport = FailIfCalledTransport()

    second = _run_real_first_prepare(tmp_path, second_transport)

    assert first["network_requests_made"] == 2
    assert first["cache_writes"] == 2
    assert second["network_requests_made"] == 0
    assert second["cache_hits"] >= 2
    assert second["cache_writes"] == 0
    assert second_transport.calls == []
```

- [ ] **Step 2: Add a report-field test for cache audit disclosure**

```python
def test_real_first_run_report_discloses_cache_replay_counters(tmp_path):
    _run_real_first_prepare(tmp_path, _successful_transport())
    result = _run_real_first_prepare(tmp_path, FailIfCalledTransport())
    report = Path(result["audit_files"]["prepare_report"]).read_text(encoding="utf-8")

    assert "cache_enabled: true" in report
    assert "cache_hits: 2" in report
    assert "cache_writes: 0" in report
    assert "cache_read_failures: 0" in report
    assert "network_requests_made: 0" in report
    assert "backtest_network_access: false" in report
    assert "request_contract: real_first_run" in report
```

- [ ] **Step 3: Tighten the existing malformed-cache test to require an audit counter**

Extend `test_real_first_run_malformed_raw_cache_is_audited_then_refetched`:

```python
    report = Path(result["audit_files"]["prepare_report"]).read_text(encoding="utf-8")
    assert "CACHE_READ_FAILED" in errors
    assert "cache_read_failures: 1" in report
    assert result["network_requests_made"] == 2
```

The expected network count remains `2` in this fixture because its metadata
entry is not pre-cached; the corrupted Baidu entry and uncached metadata each
make one fake transport call.

- [ ] **Step 4: Add stable source-error-code tests for real-client failures**

```python
def test_real_first_run_daily_failure_keeps_stable_source_error_code(tmp_path):
    result = _run_real_first_prepare(
        tmp_path,
        FakeJsonTransport(failures={"baidu_daily_kline": OSError("HTTP 503")}),
    )
    errors = Path(result["audit_files"]["source_errors"]).read_text(encoding="utf-8")

    assert "HTTP_REQUEST_FAILED" in errors
    assert "HISTORICAL_DAILY_BAR_SOURCE_FAILED" in errors


def test_real_first_run_metadata_failure_keeps_stable_source_error_code(tmp_path):
    result = _run_real_first_prepare(
        tmp_path,
        FakeJsonTransport(
            responses={"baidu_daily_kline": _baidu_payload()},
            failures={"eastmoney_stock_metadata": OSError("metadata offline")},
        ),
    )
    errors = Path(result["audit_files"]["source_errors"]).read_text(encoding="utf-8")

    assert "HTTP_REQUEST_FAILED" in errors
    assert "HISTORICAL_METADATA_FAILED" in errors
```

- [ ] **Step 5: Run focused tests to demonstrate the new requirements fail**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2d_real_request_minimal.py -q
```

Expected: failures for missing `cache_enabled` /
`cache_read_failures` report fields, missing result audit keys, and missing
stable stage-level errors. Existing cache-hit behavior may already satisfy the
zero-transport assertion.

- [ ] **Step 6: Commit the failing-test checkpoint only if the project convention permits red commits**

The established branch convention commits passing increments, so default to
leaving this checkpoint uncommitted and proceed directly to Task 2 after
recording the failing output.

## Task 2: Publish Raw Cache Replay Diagnostics

**Files:**
- Modify: `overnight_quant/backtest/astock_real_historical_client.py`
- Modify: `overnight_quant/backtest/astock_historical_source.py`

- [ ] **Step 1: Add counters to the real client**

In `AStockRealHistoricalClient.__init__`, add:

```python
self._cache_enabled = True
self._cache_read_failures = 0
```

In the malformed-cache exception branch of `_request_json`, increment the
counter before auditing:

```python
except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    self._cache_read_failures += 1
    self._add_error(symbol, "cache_read", "CACHE_READ_FAILED", str(exc))
```

Return both fields from `audit_snapshot()`:

```python
return {
    "cache_enabled": self._cache_enabled,
    "network_requests_made": self._network_requests_made,
    "cache_hits": self._cache_hits,
    "cache_writes": self._cache_writes,
    "cache_read_failures": self._cache_read_failures,
    "endpoint_attempts": dict(self._endpoint_attempts),
    "endpoint_successes": dict(self._endpoint_successes),
    "endpoint_failures": dict(self._endpoint_failures),
}
```

- [ ] **Step 2: Extend `SourceAudit` and diagnostic merging**

Add fields to `SourceAudit`:

```python
cache_enabled: bool = False
cache_read_failures: int = 0
```

Initialize the source-layer view conservatively:

```python
audit = SourceAudit(
    ...
    cache_enabled=self.use_cache,
)
```

Then let the real client's raw-cache truth override the normalized source
layer in `_merge_client_diagnostics`:

```python
audit.cache_enabled = bool(values.get("cache_enabled", audit.cache_enabled))
audit.network_requests_made = int(values.get("network_requests_made", 0))
audit.cache_hits = int(values.get("cache_hits", 0))
audit.cache_writes = int(values.get("cache_writes", 0))
audit.cache_read_failures = int(values.get("cache_read_failures", 0))
```

This matters because `real_first_run` deliberately uses
`AStockHistoricalSource(..., use_cache=False)` while its real client owns an
enabled raw-response cache.

- [ ] **Step 3: Preserve stable stage-level source errors alongside low-level real-client errors**

Replace the daily-bar empty-result check so a low-level error does not suppress
the stable source-stage code:

```python
if not accepted:
    if not _has_error_code(
        errors,
        code,
        "daily_bars_primary",
        "HISTORICAL_DAILY_BAR_SOURCE_FAILED",
    ):
        errors.append(
            _error(
                code,
                "daily_bars_primary",
                "HISTORICAL_DAILY_BAR_SOURCE_FAILED",
                "no accepted historical daily bars",
            )
        )
    continue
```

After normalizing the metadata result, add the stable metadata audit without
filling `list_date`:

```python
metadata = metadata if isinstance(metadata, dict) else {}
if not metadata and not _has_error_code(
    errors,
    code,
    "metadata",
    "HISTORICAL_METADATA_FAILED",
):
    errors.append(
        _error(
            code,
            "metadata",
            "HISTORICAL_METADATA_FAILED",
            "metadata unavailable; list_date remains unknown",
        )
    )
```

Add the focused helper next to `_has_stage_error`:

```python
def _has_error_code(
    errors: list[SourceError],
    code: str,
    stage: str,
    error_code: str,
) -> bool:
    return any(
        error.code == code
        and error.stage == stage
        and error.error_code == error_code
        for error in errors
    )
```

- [ ] **Step 4: Run focused tests**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2d_real_request_minimal.py -q
```

Expected: replay counter assertions may still fail at the report/result
surface, while client-level cache and stable source-error tests pass.

## Task 3: Expose Cache Replay Audit In Preparation Output And CLI

**Files:**
- Modify: `overnight_quant/backtest/data_preparation.py`
- Modify: `overnight_quant/scripts/prepare_backtest_data.py`
- Test: `overnight_quant/tests/test_phase32b2d_real_request_minimal.py`

- [ ] **Step 1: Add cache fields to prepared and failure result structures**

Extend `PreparationResult`:

```python
cache_enabled: bool = False
cache_read_failures: int = 0
```

When constructing the completed result, copy source audit fields:

```python
cache_enabled=bool(batch.audit.get("cache_enabled", False)),
cache_hits=int(batch.audit.get("cache_hits", 0)),
cache_writes=int(batch.audit.get("cache_writes", 0)),
cache_read_failures=int(batch.audit.get("cache_read_failures", 0)),
```

Add defaults to `_empty_astock_audit()`:

```python
"cache_enabled": request.request_contract == "real_first_run",
"cache_hits": 0,
"cache_writes": 0,
"cache_read_failures": 0,
```

Include the same fields in the CLI-facing `DataPreparationError` dictionary
assembled by `run_prepare`.

- [ ] **Step 2: Write cache fields to the manifest and prepare reports**

In `_build_manifest()` add:

```python
"cache_enabled": bool(source_audit.get("cache_enabled", False)),
"cache_hits": int(source_audit.get("cache_hits", 0)),
"cache_writes": int(source_audit.get("cache_writes", 0)),
"cache_read_failures": int(source_audit.get("cache_read_failures", 0)),
```

In both successful `_write_audit_files()` and failed
`write_failed_prepare_report()`, add lines alongside existing request
counters:

```python
# Successful report, from the persisted manifest:
f"cache_enabled: {str(bool(manifest.get('cache_enabled', False))).lower()}",
f"cache_hits: {int(manifest.get('cache_hits', 0))}",
f"cache_writes: {int(manifest.get('cache_writes', 0))}",
f"cache_read_failures: {int(manifest.get('cache_read_failures', 0))}",
f"network_requests_made: {int(manifest.get('network_requests_made', 0))}",
"backtest_network_access: false",
```

For `write_failed_prepare_report()`, use the source audit on the exception:

```python
f"cache_enabled: {str(bool(error.source_audit.get('cache_enabled', False))).lower()}",
f"cache_hits: {int(error.source_audit.get('cache_hits', 0))}",
f"cache_writes: {int(error.source_audit.get('cache_writes', 0))}",
f"cache_read_failures: {int(error.source_audit.get('cache_read_failures', 0))}",
f"network_requests_made: {int(error.source_audit.get('network_requests_made', 0))}",
```

- [ ] **Step 3: Print replay counters in the a-stock-data CLI output**

For the `args.source == "a-stock-data"` success and error output branches,
print:

```python
print(f"cache_enabled: {str(result.get('cache_enabled', False)).lower()}")
print(f"cache_hits: {result.get('cache_hits', 0)}")
print(f"cache_writes: {result.get('cache_writes', 0)}")
print(f"cache_read_failures: {result.get('cache_read_failures', 0)}")
print(f"network_requests_made: {result.get('network_requests_made', 0)}")
```

Keep the existing `real_request_enabled` line and do not expose or add an
option that disables cache for the real-first-run route.

- [ ] **Step 4: Run focused tests**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2d_real_request_minimal.py -q
```

Expected: all focused real-first-run replay and malformed-cache audit tests
pass.

## Task 4: Verify Processed Reproducibility And Offline Consumption

**Files:**
- Modify: `overnight_quant/tests/test_phase32b2d_real_request_minimal.py`

- [ ] **Step 1: Add manifest normalization and byte-hash helpers in tests**

Use a test-only normalization function; do not introduce a production
comparison subsystem:

```python
import hashlib
import yaml

VOLATILE_MANIFEST_KEYS = {
    "created_at",
    "network_requests_made",
    "cache_hits",
    "cache_writes",
    "cache_read_failures",
    "endpoint_attempts",
    "endpoint_successes",
    "endpoint_failures",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized_manifest(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key in VOLATILE_MANIFEST_KEYS:
        value.pop(key, None)
    return value
```

- [ ] **Step 2: Add a processed-output replay test**

Preserve first-run output bytes before overwriting the same processed
directory during replay:

```python
def test_real_first_run_cache_replay_preserves_processed_inputs(tmp_path):
    first = _run_real_first_prepare(tmp_path, _successful_transport())
    out_dir = Path(first["out_dir"])
    csv_names = (
        "daily_bars.csv",
        "selection_snapshots.csv",
        "market_snapshots.csv",
        "benchmark_bars.csv",
    )
    first_hashes = {name: _sha256(out_dir / name) for name in csv_names}
    first_manifest = _normalized_manifest(out_dir / "dataset_manifest.yaml")

    second = _run_real_first_prepare(tmp_path, FailIfCalledTransport())
    second_out_dir = Path(second["out_dir"])

    assert {name: _sha256(second_out_dir / name) for name in csv_names} == first_hashes
    assert _normalized_manifest(second_out_dir / "dataset_manifest.yaml") == first_manifest
```

- [ ] **Step 3: Extend offline consumption proof to begin from a replayed dataset**

Add:

```python
def test_daily_proxy_after_real_cache_replay_remains_offline(tmp_path):
    config = _config(tmp_path)
    _run_real_first_prepare(tmp_path, _successful_transport(), config=config)
    replay_transport = FailIfCalledTransport()
    prepared = _run_real_first_prepare(tmp_path, replay_transport, config=config)

    result = run_backtest(
        dataset="local",
        fidelity="daily_proxy",
        data_dir=prepared["out_dir"],
        run_id="real-cache-replay-offline",
        config=config,
    )

    assert "error" not in result
    assert replay_transport.calls == []
    summary = Path(result["output_dir"], "backtest_summary.md").read_text(encoding="utf-8")
    assert "Report Fidelity: DAILY_PROXY" in summary
```

- [ ] **Step 4: Run focused and neighboring tests**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2d_real_request_minimal.py overnight_quant/tests/test_phase32b2c_fake_real_request.py overnight_quant/tests/test_phase32b2_astock_mocked_source.py overnight_quant/tests/test_phase32b_preparation.py -q
```

Expected: all tests pass; neither fake transport nor offline backtest performs
unexpected endpoint access.

## Task 5: Document The Warm-Cache Acceptance Workflow

**Files:**
- Modify: `overnight_quant/README.md`
- Modify: `overnight_quant/backtest_data/README.md`
- Modify: `overnight_quant/RELEASE_NOTES.md`

- [ ] **Step 1: Add replay documentation to the main README**

Document that:

```text
Phase 3.2b-2d cold-fill evidence:
  network_requests_made: 2
  cache_writes: 2

Phase 3.2b-2e replay command:
  the same one-code/ten-day enabled prepare command

Replay acceptance:
  cache_enabled: true
  cache_hits >= 2
  cache_writes: 0
  network_requests_made: 0
  backtest_network_access: false
```

State explicitly that raw cache is ignored, that replay does not widen real
request scope, and that output remains `DAILY_PROXY` only.

- [ ] **Step 2: Document cache corruption testing as test-only**

In `overnight_quant/backtest_data/README.md`, add:

```text
Malformed-cache recovery is tested only against disposable pytest cache
directories with an injected fake transport. Do not corrupt or delete the
accepted real raw cache during routine replay verification.
```

- [ ] **Step 3: Add release notes**

Add Phase 3.2b-2e bullets describing:

- raw-cache replay counters;
- zero-network warm replay;
- normalized processed-output consistency verification;
- unchanged `DAILY_PROXY` and no-automation boundaries.

## Task 6: Full Verification And Manual Warm Replay

**Files:**
- No tracked file edits; generated files remain ignored.

- [ ] **Step 1: Run full tests**

Run:

```text
python -m pytest overnight_quant/tests -q
```

Expected: all tests pass.

- [ ] **Step 2: Confirm the existing cold-fill evidence without making a new request**

Read the previously generated ignored Phase 3.2b-2d audit report, or the
equivalent retained report from that accepted run:

```text
overnight_quant/backtest_data/manifests/prepare_report_20260527_193319.md
```

Required evidence:

```text
request_contract: real_first_run
network_requests_made: 2
cache_writes: 2
backtest_network_access: false
```

If that ignored file no longer exists, do not force a cold-fill request
automatically. Report that cold-fill evidence is unavailable locally and ask
for authorization before removing cache and making a replacement outbound
request.

- [ ] **Step 3: Run exactly one warm replay of the existing allowed request**

Run:

```text
python overnight_quant/scripts/prepare_backtest_data.py --source a-stock-data --enable-real-astock-request --codes 600519 --start 2025-01-02 --end 2025-01-10 --out-dir overnight_quant/backtest_data/processed --max-codes 1 --sleep 0.5 --overwrite
```

Expected CLI evidence:

```text
cache_enabled: true
cache_hits: 2
cache_writes: 0
cache_read_failures: 0
network_requests_made: 0
```

If `network_requests_made` is nonzero, stop; do not expand scope. Diagnose the
cache key or read path before any further real request.

- [ ] **Step 4: Run offline daily-proxy consumption**

Run:

```text
python overnight_quant/scripts/run_backtest.py --dataset local --fidelity daily_proxy --data-dir overnight_quant/backtest_data/processed
```

Expected:

```text
Report Fidelity: DAILY_PROXY
```

No network counter is introduced in the backtest; it consumes processed local
files only. Zero trades remain acceptable because safety and benchmark fields
are deliberately conservative.

- [ ] **Step 5: Check forbidden integrations and ignored outputs**

Run:

```text
rg -n -i "overnight_quant\.data\.astock_client|qt\.gtimg\.cn|10jqka|/fflow/|mootdx|pyautogui|selenium|broker api|auto_order|place_order|click\(" overnight_quant/backtest/astock_real_historical_client.py overnight_quant/backtest/astock_historical_source.py overnight_quant/backtest/data_preparation.py overnight_quant/scripts/prepare_backtest_data.py overnight_quant/backtest/backtest_engine.py overnight_quant/scripts/run_backtest.py
git check-ignore -v overnight_quant/backtest_data/processed/daily_bars.csv overnight_quant/backtest_data/cache/a_stock_data/* overnight_quant/backtest_data/manifests/*.md overnight_quant/backtest_outputs/*
git status --short --branch
```

Expected:

- no forbidden integration matches;
- raw cache, processed output, prepare reports, and backtest output are ignored;
- unrelated untracked workspace files remain uncommitted.

## Task 7: Precisely Commit The Phase

**Files:**
- Stage only files modified by Tasks 1-5.

- [ ] **Step 1: Review and stage exact paths**

Run:

```text
git diff --check
git add -- overnight_quant/tests/test_phase32b2d_real_request_minimal.py overnight_quant/backtest/astock_real_historical_client.py overnight_quant/backtest/astock_historical_source.py overnight_quant/backtest/data_preparation.py overnight_quant/scripts/prepare_backtest_data.py overnight_quant/README.md overnight_quant/backtest_data/README.md overnight_quant/RELEASE_NOTES.md
git diff --cached --check
git diff --cached --stat
git status --short --branch
```

Do not use `git add .`; do not stage cache, processed output, prepare reports,
backtest output, `AGENTS.md`, or `holdings.json`.

- [ ] **Step 2: Commit**

Run:

```text
git commit -m "Verify real historical cache replay determinism"
```

Expected: a commit containing only Phase 3.2b-2e source, tests, and
documentation.

## Implementation Completion Checklist

- [ ] `cache_enabled`, `cache_hits`, `cache_writes`,
  `cache_read_failures`, and `network_requests_made` are visible in
  real-first-run audit output.
- [ ] Replay tests prove cached Baidu and metadata raw responses cause zero
  transport calls.
- [ ] Malformed-cache recovery is tested with fake transport only and records
  `CACHE_READ_FAILED`.
- [ ] Processed CSV bytes and normalized manifest content are stable across
  cold fixture preparation and cache replay.
- [ ] Manual acceptance reuses the already authorized real cache rather than
  forcing a second cold network fetch.
- [ ] Offline backtest still reports `DAILY_PROXY` and performs no network
  access.
- [ ] No live/current fields, fallback data, automatic order entry, or UI
  automation has been introduced.
