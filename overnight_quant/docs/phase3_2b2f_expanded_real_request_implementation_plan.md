# Phase 3.2b-2f Expanded Real Request Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:test-driven-development` while implementing, then `superpowers:verification-before-completion` before committing. The expanded manual request is not authorized by this plan-writing step; execute it only after explicit approval.

**Goal:** Introduce an explicit, auditable opt-in from the proven one-code/ten-day real historical preparation scope to at most three codes and thirty-one natural days, while preserving cache-first preparation, offline `daily_proxy` consumption, and conservative safety handling.

**Architecture:** Reuse the existing `prepare_backtest_data.py` -> `PreparationRequest` -> `AStockHistoricalSource` -> `AStockRealHistoricalClient` pipeline. Add one CLI/request dimension, `real_request_scope`, whose default preserves the current minimal contract and whose `expanded` value permits only the already defined outer hard limits. No endpoint, truth-level rule, safety default, cache namespace, backtest engine behavior, or execution behavior is expanded by this phase.

**Tech Stack:** Python standard library, existing `overnight_quant.backtest` preparation modules, raw JSON cache under `backtest_data/cache/a_stock_data/`, `pytest`, and existing offline `daily_proxy` reports.

---

## Scope Lock

This phase may implement only:

```text
--real-request-scope minimal|expanded
  -> validated limits and failure audit
  -> per-code preparation/source-result disclosure
  -> expanded fake-transport and cache replay tests
  -> later, separately approved three-code real preparation verification
```

It must not implement or enable:

- any request above three explicit codes or thirty-one natural days;
- implicit code truncation, reordering, or selection of a subset;
- a new historical endpoint, benchmark retrieval, fund flow, THS themes, or Tencent current quote;
- any live adapter import, demo/sample fallback, or positive fixture fallback for the real path;
- network access from `run_backtest.py`;
- automatic ordering, broker integration, software clicking, GUI, machine learning, or parameter optimization.

The existing endpoint boundary remains:

| Capability | Phase 3.2b-2f Status |
| --- | --- |
| Baidu dated stock daily K-line | Allowed during explicitly enabled preparation |
| Eastmoney stable metadata (`name`, `list_date`) | Allowed during explicitly enabled preparation |
| Raw response cache and source error auditing | Required |
| Benchmark retrieval | Not added; may remain `BENCHMARK_UNAVAILABLE` |
| Historical theme, capital flow, tail-minute data | `UNAVAILABLE` |
| Current/live quote, hot reason, fund flow | Forbidden |

## Scope Parameter Design

Adopt Option A:

```text
--real-request-scope minimal|expanded
```

Rules:

- The option applies only when `--source a-stock-data` is selected.
- Its default is `minimal`, preserving current behavior and accepted cache replay tests.
- Selecting `expanded` requires `--enable-real-astock-request`, including for dry-run validation; selecting `expanded` alone returns `REAL_NETWORK_NOT_ENABLED`.
- A dry-run with the required enable flag may render either scope plan, but must make zero transport/network calls.
- `--source sample` and `--source local-raw` ignore this option or reject its use with a CLI validation message; it must not alter offline datasets.

Use these fixed constants without deleting the accepted safeguards:

```python
REAL_REQUEST_HARD_MAX_CODES = 3
REAL_REQUEST_MAX_DAYS = 31
REAL_FIRST_RUN_MAX_CODES = 1
REAL_FIRST_RUN_MAX_DAYS = 10

REAL_SCOPE_MINIMAL = "minimal"
REAL_SCOPE_EXPANDED = "expanded"
REAL_SCOPE_LIMITS = {
    REAL_SCOPE_MINIMAL: (REAL_FIRST_RUN_MAX_CODES, REAL_FIRST_RUN_MAX_DAYS),
    REAL_SCOPE_EXPANDED: (REAL_REQUEST_HARD_MAX_CODES, REAL_REQUEST_MAX_DAYS),
}
```

### Behavior Table

| Scope / Invocation | Allowed Codes | Allowed Natural Days | Enable Flag Required To Fetch | Failure When Over Scope |
| --- | ---: | ---: | --- | --- |
| default `minimal` | 1 | 10 | Yes | `REAL_FIRST_RUN_SCOPE_TOO_LARGE` |
| explicit `minimal` | 1 | 10 | Yes | `REAL_FIRST_RUN_SCOPE_TOO_LARGE` |
| explicit `expanded` | 3 | 31 | Yes, including dry-run validation | `MAX_CODES_EXCEEDS_REAL_REQUEST_LIMIT` or `DATE_RANGE_EXCEEDS_REAL_REQUEST_LIMIT` |
| either enabled scope with `--dry-run` | Same validation limits | Same validation limits | Flag validates intent; no fetch permitted | Same scope error, zero requests |

Validation order for `--source a-stock-data`:

1. Require codes and a valid date range.
2. Enforce `--sleep >= 0.2`.
3. Resolve the requested scope and require `--enable-real-astock-request` when `expanded` is selected, even for dry-run.
4. Reject scope excess before constructing a client or reading/writing raw cache.
5. For any non-dry real operation, require `--enable-real-astock-request`.
6. Check overwrite/output conditions.
7. Only then construct or call the existing real preparation client.

For `expanded`, the immutable hard-limit errors remain:

```text
MAX_CODES_EXCEEDS_REAL_REQUEST_LIMIT
DATE_RANGE_EXCEEDS_REAL_REQUEST_LIMIT
```

No input code is silently discarded, and original input ordering is retained in request and audit output.

## Compatibility And Cache Contract

The current accepted implementation uses:

```text
request_contract: real_first_run
ASTOCK_REAL_ENDPOINT_VERSION: real_historical_first_run_v1
```

The expansion must not invalidate already proven raw cache entries. The endpoint response shape and the cache-key dimensions remain the same:

```text
source / endpoint_version / symbol / requested date range / request hash
```

Implementation guidance:

- Keep the existing raw endpoint cache version while the endpoint/schema is unchanged, even though its historical name mentions `first_run`.
- Add `real_request_scope` as an audit/request policy field, not as a raw response cache-key dimension.
- For compatibility, `minimal` may continue emitting `request_contract: real_first_run`.
- For `expanded`, emit a distinct policy contract such as `request_contract: real_expanded_validation`, while routing through the same endpoint client and cache schema.
- A wider date range is a different cache key by design: a cached `600519` request for `2025-01-02` through `2025-01-10` does not eliminate a new request for `2025-01-02` through `2025-01-31`.
- Repeating the exact same expanded code/date/source request must be cache-only on replay.

No plan step clears or mutates the accepted minimal cache to manufacture new requests.

## Truth Levels And Safety Policy

The `a-stock-data` preparation path remains restricted to:

```text
REAL_HISTORICAL
DAILY_PROXY
UNAVAILABLE
UNKNOWN
```

`SAMPLE_FIXTURE` must not occur anywhere in an enabled real-request manifest or processed output.

| Field Group | Truth Level / Handling |
| --- | --- |
| dated stock OHLCV accepted inside requested interval | `REAL_HISTORICAL` |
| stable metadata `name`, `list_date` when returned | stable metadata disclosure, not intraday market evidence |
| `vol_ratio`, `range_position`, market gate proxy | `DAILY_PROXY` |
| `theme_tags`, `theme_rank`, `main_net`, `big_order_net`, `tail_pullback_pct` | `UNAVAILABLE` |
| `limit_up`, `limit_down`, historical `is_st`, historical `is_suspended` if not returned by a dated reliable source | `UNKNOWN`; downstream rejects |
| `is_bj_stock` derived from code prefix | disclosed proxy |

The suggested expanded trial pool may include `600519`, `300750`, and `510300`, but the implementation must not hard-code them. ETF metadata or safety gaps are acceptable and remain conservative rejections. A zero-trade `daily_proxy` result is a valid data-pipeline outcome.

## Audit And Report Contract

For both successful and failed `a-stock-data` preparation attempts, extend the preparation report/manifest/result payload where applicable with:

```text
real_request_scope: minimal|expanded
requested_codes_count:
effective_real_request_max_codes:
requested_date_range_days:
max_allowed_days:
cache_enabled:
cache_hits:
cache_writes:
cache_read_failures:
network_requests_made:
partial_success:
failed_codes:
backtest_network_access: false
real_request_enabled:
request_contract:
```

Add per-code source outcome disclosure to the report, either as a Markdown
table or a serializable audit structure:

| Code | Daily Bars | Metadata | Cache/Network Outcome | Safety Unknown Notes |
| --- | --- | --- | --- | --- |
| requested code | `SUCCESS` / `FAILED` | `SUCCESS` / `FAILED` | `CACHE_HIT` / `FETCHED` / `FAILED` | relevant unknown safety fields |

Rules:

- `partial_success: true` means at least one requested code produced accepted dated bars while at least one requested code failed an attempted required source stage.
- `failed_codes` records requested codes for which no accepted dated stock bars were written; it does not silently remove failures from the report.
- Existing `source_errors.csv` remains the detailed error stream and retains stable codes including `CACHE_READ_FAILED`, `HISTORICAL_METADATA_FAILED`, `HISTORICAL_DAILY_BAR_SOURCE_FAILED`, and `BENCHMARK_UNAVAILABLE`.
- All reports continue to state `DAILY_PROXY only`, `NOT_STRICT_HISTORICAL`, and `backtest_network_access: false`.

## File Map

Implementation should be constrained to these paths:

| Path | Responsibility In This Plan |
| --- | --- |
| `overnight_quant/tests/test_phase32b2d_real_request_minimal.py` | Retain minimal compatibility/replay assertions; add a minimal default regression only if useful |
| `overnight_quant/tests/test_phase32b2f_expanded_real_request_scope.py` | New TDD coverage for scope validation, fake expanded preparation, partial success, cache replay, and offline consumption |
| `overnight_quant/backtest/astock_historical_source.py` | Add scope/audit/per-code outcome structure while reusing the existing endpoint/cache behavior |
| `overnight_quant/backtest/data_preparation.py` | Validate scope, carry limits, and render expanded audit fields/reports/manifests |
| `overnight_quant/scripts/prepare_backtest_data.py` | Parse `--real-request-scope`, preserve default minimal behavior, and print scope/audit summary |
| `overnight_quant/README.md` | Describe explicit minimal vs expanded opt-in and offline-only backtest use |
| `overnight_quant/backtest_data/README.md` | Document exact-range cache semantics and ignored expanded preparation outputs |
| `overnight_quant/RELEASE_NOTES.md` | Record controlled scope expansion after cache replay validation |

Do not modify scoring, risk, execution, sell-plan, lifecycle, trade-review, live scan, or backtest engine/fee logic.

## Task 1: Lock Scope Behavior With Failing Tests

**Files:**
- Create: `overnight_quant/tests/test_phase32b2f_expanded_real_request_scope.py`
- Confirm unchanged behavior in: `overnight_quant/tests/test_phase32b2d_real_request_minimal.py`

- [ ] **Step 1: Add default minimal regression tests**

Verify the default remains restrictive even after a scope option is added:

```python
def test_default_minimal_scope_rejects_two_codes_before_any_client_call(tmp_path):
    result = run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        codes=["600519", "300750"],
        start="2025-01-02",
        end="2025-01-10",
        max_codes=3,
        dry_run=True,
        config=_config(tmp_path),
    )

    assert result["error"] == "REAL_FIRST_RUN_SCOPE_TOO_LARGE"
    assert result["network_requests_made"] == 0
    assert result["real_request_scope"] == "minimal"
```

Add the matching over-ten-day minimal case.

- [ ] **Step 2: Add expanded validation tests**

Verify `expanded` accepts its exact boundary and rejects inputs above it:

```python
def test_expanded_scope_accepts_three_codes_and_thirty_one_days_in_dry_run(tmp_path):
    result = run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        real_request_scope="expanded",
        codes=["600519", "300750", "510300"],
        start="2025-01-01",
        end="2025-01-31",
        max_codes=3,
        dry_run=True,
        config=_config(tmp_path),
    )

    assert result["status"] == "DRY_RUN"
    assert result["network_requests_made"] == 0
    assert result["effective_real_request_max_codes"] == 3
    assert result["max_allowed_days"] == 31
```

Add rejection cases for four codes and thirty-two natural days asserting:

```text
MAX_CODES_EXCEEDS_REAL_REQUEST_LIMIT
DATE_RANGE_EXCEEDS_REAL_REQUEST_LIMIT
```

- [ ] **Step 3: Add enable-flag and no-truncation tests**

An expanded invocation without `--enable-real-astock-request`, including
dry-run, must remain unable to validate an expanded real-request plan or
create a real client, and must not rewrite the requested stocks:

```python
assert result["error"] == "REAL_NETWORK_NOT_ENABLED"
assert result["network_requests_made"] == 0
```

For an over-limit input, assert the report preserves
`requested_codes_count: 4` and `effective_real_request_max_codes: 3`.

- [ ] **Step 4: Run the focused tests to show they fail before implementation**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2f_expanded_real_request_scope.py -q
```

Expected: failures because `real_request_scope` is not yet a supported request
or CLI field.

## Task 2: Implement The Scope Model And Validation Gate

**Files:**
- Modify: `overnight_quant/backtest/astock_historical_source.py`
- Modify: `overnight_quant/backtest/data_preparation.py`
- Modify: `overnight_quant/scripts/prepare_backtest_data.py`

- [ ] **Step 1: Add explicit scope constants and request/result fields**

Add:

```python
REAL_SCOPE_MINIMAL = "minimal"
REAL_SCOPE_EXPANDED = "expanded"
REAL_SCOPE_LIMITS = {
    REAL_SCOPE_MINIMAL: (REAL_FIRST_RUN_MAX_CODES, REAL_FIRST_RUN_MAX_DAYS),
    REAL_SCOPE_EXPANDED: (REAL_REQUEST_HARD_MAX_CODES, REAL_REQUEST_MAX_DAYS),
}
```

Carry `real_request_scope` in `PreparationRequest`, `PreparationResult`,
CLI output, successful manifests, and failed preparation reports.

- [ ] **Step 2: Route validation through selected scope**

Keep the outer constants immutable. In `_validate_request`, compute the
scope-specific limits only after common validation and sleep validation.

Expected behavior:

```python
if scope == "minimal" and input_exceeds_minimal:
    raise DataPreparationError("REAL_FIRST_RUN_SCOPE_TOO_LARGE", ...)
if scope == "expanded" and len(codes) > REAL_REQUEST_HARD_MAX_CODES:
    raise DataPreparationError("MAX_CODES_EXCEEDS_REAL_REQUEST_LIMIT", ...)
if scope == "expanded" and days > REAL_REQUEST_MAX_DAYS:
    raise DataPreparationError("DATE_RANGE_EXCEEDS_REAL_REQUEST_LIMIT", ...)
```

Do not slice `codes`; use validation only.

- [ ] **Step 3: Preserve default minimal real client construction**

The no-option path must continue through the accepted one-code/ten-day
client and cache replay behavior. For `expanded`, construct the same concrete
client class only after all validation and explicit enable checks pass. Keep
the cache endpoint version unchanged so an exact cached endpoint response is
still reusable.

- [ ] **Step 4: Add CLI option and summary fields**

Extend the parser:

```python
parser.add_argument(
    "--real-request-scope",
    choices=["minimal", "expanded"],
    default="minimal",
)
```

For `a-stock-data`, print:

```text
real_request_scope: expanded
effective_real_request_max_codes: 3
max_allowed_days: 31
cache_hits: ...
cache_writes: ...
cache_read_failures: ...
network_requests_made: ...
```

- [ ] **Step 5: Run scope validation tests**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2f_expanded_real_request_scope.py -q
python -m pytest overnight_quant/tests/test_phase32b2d_real_request_minimal.py -q
```

Expected: passing; minimal cache/replay behavior remains intact.

## Task 3: Add Per-Code Source Outcomes And Partial-Success Audit

**Files:**
- Modify: `overnight_quant/backtest/astock_historical_source.py`
- Modify: `overnight_quant/backtest/data_preparation.py`
- Modify: `overnight_quant/tests/test_phase32b2f_expanded_real_request_scope.py`

- [ ] **Step 1: Write fake-transport partial-success tests first**

Use injected fake transport responses for three explicit codes: one with
accepted daily bars and stable metadata, one daily-bars failure, and one bars
success with metadata failure. No test may use `UrllibJsonTransport`.

Assert:

```python
assert result["status"] == "PARTIAL_DATA_PREPARED"
assert result["partial_success"] is True
assert result["failed_codes"] == ["300750"]
assert set(_processed_codes(result)) == {"600519", "510300"}
```

Assert that missing metadata does not invent `list_date`, and downstream
safety handling remains conservative.

- [ ] **Step 2: Add per-code audit output**

Extend the source audit with an ordered per-code summary sufficient to render:

```text
code,daily_bars_status,metadata_status,daily_bars_origin,metadata_origin,safety_unknown_fields
```

The report may render a Markdown table rather than introduce another CSV;
`source_errors.csv` remains the canonical detailed error file.

- [ ] **Step 3: Add preparation report fields**

For expanded success, partial success, and failure reports, emit:

```text
partial_success: true|false
failed_codes: ...
per_code_source_status:
```

For no successful daily bars, retain `NO_DAILY_BARS_FETCHED` and list all
failed codes in the failed report.

- [ ] **Step 4: Run audit-focused tests**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2f_expanded_real_request_scope.py -q
```

Expected: all expanded fake-client reporting assertions pass without a real
network request.

## Task 4: Prove Expanded Cache Replay And Offline Consumption

**Files:**
- Modify: `overnight_quant/tests/test_phase32b2f_expanded_real_request_scope.py`

- [ ] **Step 1: Add exact-request expanded cache replay test**

Populate a temporary raw cache through injected fake transport, repeat the
identical expanded preparation with a transport that raises if called, and
assert:

```python
assert replay["network_requests_made"] == 0
assert replay["cache_hits"] >= successful_endpoint_count
assert replay["cache_writes"] == 0
assert replay["cache_read_failures"] == 0
```

The test must compare identical code list and date range. It must not expect a
cache hit from the accepted ten-day real cache when testing a thirty-one-day
range.

- [ ] **Step 2: Add processed-output consistency assertion**

Compare generated processed CSV files byte-for-byte between fake cold fill and
cache replay. Compare `dataset_manifest.yaml` after removing timestamp and
cache/network counter keys, retaining `real_request_scope: expanded`.

- [ ] **Step 3: Run offline daily_proxy on the expanded prepared fixture**

Invoke existing `run_backtest` only after preparation has completed. Assert
the injected transport call count does not change and that the summary states:

```text
Report Fidelity: DAILY_PROXY
strict_historical is not implemented
```

Do not assert a trade occurs. Conservative unknown safety fields and missing
benchmark may produce zero trades.

- [ ] **Step 4: Add forbidden-integration assertion**

Extend the existing production-path text scan to cover this phase's modified
paths and reject imports or URLs for live/current or execution facilities:

```text
overnight_quant.data.astock_client
qt.gtimg.cn
10jqka
/fflow/
mootdx
pyautogui
selenium
place_order
auto_order
```

- [ ] **Step 5: Run the focused offline suite**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b2f_expanded_real_request_scope.py -q
python -m pytest overnight_quant/tests/test_phase32b2d_real_request_minimal.py -q
```

Expected: all tests pass with fake transports and local files only.

## Task 5: Update Documentation And Release Notes

**Files:**
- Modify: `overnight_quant/README.md`
- Modify: `overnight_quant/backtest_data/README.md`
- Modify: `overnight_quant/RELEASE_NOTES.md`

- [ ] **Step 1: Document scope opt-in and strict limits**

Add a small table documenting:

```text
minimal (default): 1 code / 10 natural days
expanded: 3 codes / 31 natural days
```

State that `expanded` needs `--enable-real-astock-request`, remains an
experimental preparation command, and is not an invitation to broad scans.

- [ ] **Step 2: Document cache-range behavior**

Clarify that an exact code/date/endpoint repeat is replayable from cache, but
a longer date range intentionally results in a different raw-cache key.

- [ ] **Step 3: Preserve the research limitation wording**

Documentation and reports must state:

```text
DAILY_PROXY only
NOT_STRICT_HISTORICAL
No historical theme/capital/tail reconstruction is performed.
Backtesting reads processed files offline and never performs preparation requests.
```

- [ ] **Step 4: Record release scope**

Add a Phase 3.2b-2f note describing explicit expanded-scope gating and audit
coverage, without claiming strategy validation or profitability.

## Task 6: Verification And Deferred Manual Trial

**Files:**
- No new implementation files; verify only.

- [ ] **Step 1: Run the full automated test suite**

Run:

```text
python -m pytest overnight_quant/tests -q
```

Expected: all tests pass, including existing minimal/cache replay tests and new
expanded fake-transport tests.

- [ ] **Step 2: Verify expanded dry-run remains network-free**

Run:

```text
python overnight_quant/scripts/prepare_backtest_data.py --source a-stock-data --enable-real-astock-request --real-request-scope expanded --codes 600519,300750,510300 --start 2025-01-02 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed --max-codes 3 --sleep 0.5 --dry-run
```

Expected:

```text
DRY_RUN
real_request_scope: expanded
effective_real_request_max_codes: 3
max_allowed_days: 31
network_requests_made: 0
```

- [ ] **Step 3: Verify outer hard-limit failures without transport calls**

Use dry-run inputs with four codes and with thirty-two natural days. Expected:

```text
MAX_CODES_EXCEEDS_REAL_REQUEST_LIMIT
DATE_RANGE_EXCEEDS_REAL_REQUEST_LIMIT
network_requests_made: 0
```

- [ ] **Step 4: Defer the actual expanded real request pending explicit approval**

Only after a later approval to send new outbound preparation requests, run:

```text
python overnight_quant/scripts/prepare_backtest_data.py --source a-stock-data --enable-real-astock-request --real-request-scope expanded --codes 600519,300750,510300 --start 2025-01-02 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed --max-codes 3 --sleep 0.5 --overwrite
```

Then, offline only:

```text
python overnight_quant/scripts/run_backtest.py --dataset local --fidelity daily_proxy --data-dir overnight_quant/backtest_data/processed
```

The accepted outcome may contain zero trades. Acceptance concerns bounded
request behavior, cache/audit output, processed loadability, and explicit
`DAILY_PROXY` limitations.

- [ ] **Step 5: Review generated/ignored boundaries before staging**

Confirm that raw cache, processed outputs, audit reports, and
`backtest_outputs/` remain ignored and unstaged. Do not stage task-external
untracked files.

## Planned Test Matrix

| Test | Purpose | Network Behavior |
| --- | --- | --- |
| Default minimal rejects two codes | Preserve safe default | Zero client calls |
| Default minimal rejects over ten days | Preserve safe default | Zero client calls |
| Expanded accepts three codes / thirty-one days | Opt-in boundary works | Dry-run: zero calls |
| Expanded rejects four codes | Outer hard max cannot be bypassed | Zero client calls |
| Expanded rejects over thirty-one days | Outer date max cannot be bypassed | Zero client calls |
| Expanded dry-run with enable | Explicit intent is required but dry-run cannot fetch | Zero client calls |
| Expanded without enable is rejected | Scope never grants request intent by itself | Zero client calls |
| Expanded fake partial success | Processed + audit behavior | Injected fake transport only |
| Expanded exact cache replay | Repeat fetch avoided | Replay: zero fake transport calls |
| Expanded offline daily_proxy | Backtest isolation | Zero preparation transport calls after prepare |
| Forbidden import/code scan | No live/current/execution feature introduced | File scan only |

## Commit Discipline For Future Implementation

When implementation is authorized and verified:

```text
git add -- overnight_quant/tests/test_phase32b2f_expanded_real_request_scope.py
git add -- overnight_quant/backtest/astock_historical_source.py
git add -- overnight_quant/backtest/data_preparation.py
git add -- overnight_quant/scripts/prepare_backtest_data.py
git add -- overnight_quant/README.md
git add -- overnight_quant/backtest_data/README.md
git add -- overnight_quant/RELEASE_NOTES.md
git add -- overnight_quant/docs/phase3_2b2f_expanded_real_request_implementation_plan.md
git commit -m "Add expanded bounded real historical request scope"
```

Never use `git add .`, and never commit raw cache, processed CSV files,
prepare/audit reports, backtest output runs, real trading-state records, or
task-external untracked files.
