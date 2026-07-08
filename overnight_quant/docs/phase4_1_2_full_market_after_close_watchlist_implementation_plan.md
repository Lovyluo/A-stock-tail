# Phase 4.1.2 Full-Market After-Close Watchlist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert after-close observation from hot-list top-30 review into a 00/60 full-market base scan, and restrict formal Live tail scan to 00/60 stocks.

**Architecture:** Keep the tail-session `get_candidate_quotes()` path unchanged, but add a dedicated `get_after_close_universe_quotes()` data path for `AfterCloseAnalyzer`. The new path fetches a broad Eastmoney 00/60 base universe, applies cheap base filtering, then enriches only the surviving rows with the existing live normalization/enrichment pipeline. The formal tail scan receives an independent code-prefix hard filter so Live buy signals cannot use non-00/60 stocks.

**Tech Stack:** Python, pytest, existing `AStockClient`, `AfterCloseAnalyzer`, `initial_filter`, Eastmoney clist, Tencent quote enrichment, existing report writers.

---

## File Map

- Modify `overnight_quant/data/astock_client.py`
  - Add `get_after_close_universe_quotes()`.
  - Add Eastmoney 00/60 full-market base fetch.
  - Add base universe filter and source audit.
  - Allow `_build_live_candidates()` to accept a caller-supplied limit.
- Modify `overnight_quant/strategy/after_close_analysis.py`
  - Prefer `client.get_after_close_universe_quotes()` when available.
  - Keep fallback behavior for old test clients that only expose `get_candidate_quotes()`.
- Modify `overnight_quant/strategy/filters.py`
  - Add 00/60 hard filter for Live/tail scan shared initial filter.
- Modify `overnight_quant/strategy/yang_yongxing_overnight.py`
  - Add `allowed_code_prefixes` fallback config.
- Modify `overnight_quant/config.yaml`
  - Add `filters.allowed_code_prefixes: ["00", "60"]`.
  - Add after-close full-market scan caps.
- Modify tests:
  - `overnight_quant/tests/test_filters.py`
  - `overnight_quant/tests/test_phase41_after_close_analysis.py`
  - `overnight_quant/tests/test_live_data_quality.py` or a new focused test if needed.

## Task 1: Add 00/60 hard filter for Live scan

**Files:**
- Modify: `overnight_quant/strategy/filters.py`
- Modify: `overnight_quant/strategy/yang_yongxing_overnight.py`
- Modify: `overnight_quant/config.yaml`
- Test: `overnight_quant/tests/test_filters.py`

- [ ] **Step 1: Write failing tests**

Add tests proving `initial_filter()` rejects non-00/60 stocks and accepts allowed prefixes:

```python
def test_initial_filter_rejects_non_00_60_universe_codes():
    config = load_config()
    assert "code_prefix_not_allowed" in initial_filter(by_code("300001"), config)["reject_reasons"]
    assert "code_prefix_not_allowed" in initial_filter(by_code("830009"), config)["reject_reasons"]
    assert "code_prefix_not_allowed" not in initial_filter(by_code("002005"), config)["reject_reasons"]
    assert "code_prefix_not_allowed" not in initial_filter(by_code("600003"), config)["reject_reasons"]
```

- [ ] **Step 2: Run red test**

Run:

```bash
python -m pytest overnight_quant/tests/test_filters.py::test_initial_filter_rejects_non_00_60_universe_codes -q
```

Expected: FAIL because `code_prefix_not_allowed` is not implemented.

- [ ] **Step 3: Implement minimal code**

Add `filters.allowed_code_prefixes` to defaults and YAML, then have `initial_filter()` reject codes not starting with those prefixes.

- [ ] **Step 4: Run green test**

Run:

```bash
python -m pytest overnight_quant/tests/test_filters.py::test_initial_filter_rejects_non_00_60_universe_codes -q
```

Expected: PASS.

## Task 2: Route after-close analyzer to full-market universe method

**Files:**
- Modify: `overnight_quant/strategy/after_close_analysis.py`
- Test: `overnight_quant/tests/test_phase41_after_close_analysis.py`

- [ ] **Step 1: Write failing test**

Add a stub client that exposes both `get_candidate_quotes()` and `get_after_close_universe_quotes()`, where the hot candidate is a 30-prefix stock and the universe row is a 00-prefix stock. Assert after-close uses the universe method.

```python
def test_after_close_prefers_full_market_universe_over_hot_candidates(tmp_path):
    hot = copy.deepcopy(demo_quotes()[0])
    hot["code"] = "300001"
    universe = copy.deepcopy(demo_quotes()[4])
    universe["code"] = "002005"
    client = StubAfterCloseClient("live", [hot])
    client.get_after_close_universe_quotes = lambda: [universe]

    result = run_after_close_analysis(
        mode="live",
        now=datetime(2026, 5, 22, 15, 30, tzinfo=CN_TZ),
        config=_tmp_config(tmp_path),
        client=client,
    )

    evaluated_codes = {row["code"] for row in result["evaluated_rows"]}
    assert evaluated_codes == {"002005"}
```

- [ ] **Step 2: Run red test**

Run:

```bash
python -m pytest overnight_quant/tests/test_phase41_after_close_analysis.py::test_after_close_prefers_full_market_universe_over_hot_candidates -q
```

Expected: FAIL because analyzer still calls `get_candidate_quotes()`.

- [ ] **Step 3: Implement minimal code**

In `AfterCloseAnalyzer.analyze()`, use:

```python
if hasattr(self.client, "get_after_close_universe_quotes"):
    rows = self.client.get_after_close_universe_quotes()
else:
    rows = self.client.get_candidate_quotes()
```

- [ ] **Step 4: Run green test**

Run the same test. Expected: PASS.

## Task 3: Add AStockClient after-close 00/60 universe path

**Files:**
- Modify: `overnight_quant/data/astock_client.py`
- Test: `overnight_quant/tests/test_live_data_quality.py` or `overnight_quant/tests/test_phase41_after_close_analysis.py`

- [ ] **Step 1: Write failing tests**

Add tests that monkeypatch `_get_json`, `_tencent_quotes`, and enrichment helpers to prove:

- `get_after_close_universe_quotes()` only returns 00/60 rows;
- non-00/60 rows are dropped before enrichment;
- hot top-30 truncation is not used.

Representative assertion:

```python
def test_after_close_universe_fetch_filters_to_00_and_60_before_enrichment(monkeypatch):
    client = AStockClient(mode="live", now=datetime(2026, 5, 22, 15, 30, tzinfo=CN_TZ))
    # Eastmoney fake rows include 00, 60, 30, 68, ETF-like 51.
    # Assert resulting codes only include 000001 and 600001.
```

- [ ] **Step 2: Run red tests**

Run the focused test. Expected: FAIL because the method does not exist.

- [ ] **Step 3: Implement data path**

Implement:

- `get_after_close_universe_quotes()`
- `_eastmoney_after_close_universe_seeds()`
- `_after_close_base_candidate_passes()`

Use Eastmoney clist fields:

```text
f2 price
f3 change_pct
f6 amount
f8 turnover_pct
f10 vol_ratio
f12 code
f14 name
f15 high
f16 low
f17 open
f21 float_mcap
f124 timestamp
```

Base query uses:

```text
fs=m:0+t:6,m:1+t:2
fields=f2,f3,f6,f8,f10,f12,f14,f15,f16,f17,f21,f124
```

Then filter to `00` / `60` before enrichment.

- [ ] **Step 4: Reuse existing enrichment**

Call `_build_live_candidates(filtered_seeds, limit=after_close_enrich_limit)` so Tencent quote, list date, safety, freshness, and fund flow handling stay centralized.

- [ ] **Step 5: Run green tests**

Run focused tests. Expected: PASS.

## Task 4: Preserve after-close semantics and reports

**Files:**
- Modify: `overnight_quant/reports/after_close_report.py` only if needed for source naming.
- Test: `overnight_quant/tests/test_phase41_after_close_analysis.py`

- [ ] **Step 1: Write tests**

Assert the report/result source states full-market source:

```python
assert result["candidate_source"] in {"full_market_00_60", "demo"}
```

and official CSV still excludes C rows.

- [ ] **Step 2: Implement result source naming**

When live after-close universe is used, set:

```text
candidate_source: full_market_00_60
```

If fallback occurs, keep:

```text
candidate_source: demo_fallback
```

- [ ] **Step 3: Run tests**

Run:

```bash
python -m pytest overnight_quant/tests/test_phase41_after_close_analysis.py -q
```

Expected: PASS.

## Task 5: Full verification

**Files:** all touched files.

- [ ] **Step 1: Run focused tests**

```bash
python -m pytest overnight_quant/tests/test_filters.py overnight_quant/tests/test_phase41_after_close_analysis.py -q
```

- [ ] **Step 2: Run full suite**

```bash
python -m pytest overnight_quant/tests -q
```

- [ ] **Step 3: Run demo after-close**

```bash
python overnight_quant/scripts/run_after_close_analysis.py --mode demo
```

Expected: demo still writes examples outputs and does not create manual ticket.

- [ ] **Step 4: Check forbidden code**

```bash
rg -n -i "pyautogui|selenium|broker api|auto_order|place_order|自动下单|自动点击" overnight_quant
```

Expected: production code has no automatic trading or clicking implementation.

## Commit Plan

Use precise staging only.

Commit implementation with:

```bash
git add -- \
  overnight_quant/docs/phase4_1_2_full_market_after_close_watchlist_implementation_plan.md \
  overnight_quant/data/astock_client.py \
  overnight_quant/strategy/after_close_analysis.py \
  overnight_quant/strategy/filters.py \
  overnight_quant/strategy/yang_yongxing_overnight.py \
  overnight_quant/config.yaml \
  overnight_quant/tests/test_filters.py \
  overnight_quant/tests/test_phase41_after_close_analysis.py

git commit -m "Add 00/60 full-market after-close watchlist universe"
```

Do not stage:

- `AGENTS.md`
- `overnight_quant_实盘使用手册.md`
- runtime records/reports/cache/backtest_outputs
- unrelated dashboard/i18n changes unless they are intentionally committed in a separate commit.
