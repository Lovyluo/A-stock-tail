# Phase 3.2b-1.5 Positive Prepared Fixture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicitly selected deterministic positive sample profile that proves prepared `daily_proxy` data can complete at least one trade with full reporting and fees.

**Architecture:** Keep neutral sample preparation unchanged and route `--sample-profile positive` to a separate committed offline fixture. Extend only preparation schema/report propagation so positive fixture theme/capital fields retain an explicit `sample_fixture` source; reuse the existing provider, engine, scoring, risk, and fee code unchanged.

**Tech Stack:** Python standard library, CSV/YAML text fixtures, existing `overnight_quant` preparation/provider/report modules, `pytest`.

---

### Task 1: Lock Profile Contracts With Failing Tests

**Files:**
- Modify: `overnight_quant/tests/test_phase32b_preparation.py`

- [ ] **Step 1: Add neutral and positive behavior tests**

Add tests which call:

```python
neutral = run_prepare(
    source="sample", sample_profile="neutral", codes=["300201"],
    start="2025-01-01", end="2025-01-31",
    out_dir=config["backtest"]["local_data_dir"], overwrite=True, config=config,
)
positive = run_prepare(
    source="sample", sample_profile="positive", codes=["300201"],
    start="2025-01-01", end="2025-01-31",
    out_dir=config["backtest"]["local_data_dir"], overwrite=True, config=config,
)
```

Assert that neutral remains trade-free and that positive manifest/report
contain `sample_profile: positive`, `sample_fixture`, `DAILY_PROXY_ONLY`, and
`NOT_STRICT_HISTORICAL`. Assert prepared positive selection rows retain empty
`tail_pullback_pct` plus `theme_source` and `capital_source` set to
`sample_fixture`.

- [ ] **Step 2: Add positive trade lifecycle assertions**

Run the existing callable backtest route against positive processed data:

```python
backtest = run_backtest(
    dataset="local", fidelity="daily_proxy", data_dir=positive["out_dir"],
    run_id="prepared-positive", config=config,
)
```

Assert `backtest["metrics"]["trade_count"] >= 1`, and assert each required
trade field is present, with:

```python
assert trade["selection_as_of"] == "daily_close_proxy"
assert trade["data_fidelity"] == "daily_proxy"
```

Also assert `data_quality.md` and `backtest_summary.md` carry positive fixture
pipeline-validation disclosure without claiming strict history or profits.

- [ ] **Step 3: Run tests to verify RED**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b_preparation.py -q
```

Expected: failures because `run_prepare()` does not accept `sample_profile`
and no positive fixture/report disclosure exists yet.

### Task 2: Add Profile Routing And Offline Positive Fixture

**Files:**
- Modify: `overnight_quant/scripts/prepare_backtest_data.py`
- Modify: `overnight_quant/backtest/data_preparation.py`
- Modify: `overnight_quant/config.yaml`
- Modify: `overnight_quant/strategy/yang_yongxing_overnight.py`
- Create: `overnight_quant/examples/historical_prepare_positive_raw/daily_bars.csv`
- Create: `overnight_quant/examples/historical_prepare_positive_raw/selection_snapshots.csv`
- Create: `overnight_quant/examples/historical_prepare_positive_raw/benchmark_bars.csv`

- [ ] **Step 1: Extend preparation request and CLI**

Add:

```python
sample_profile: str = "neutral"
```

to `PreparationRequest`, expose:

```python
parser.add_argument("--sample-profile", default="neutral", choices=["neutral", "positive"])
```

and resolve the positive source directory from:

```yaml
preparation_positive_sample_dir: overnight_quant/examples/historical_prepare_positive_raw
```

Only choose that directory when `source == "sample"` and
`sample_profile == "positive"`; local-raw ignores this argument.

- [ ] **Step 2: Preserve only explicit positive fixture enhanced fields**

Extend `selection_snapshots.csv` output fields with:

```python
"theme_source", "capital_source"
```

For positive sample rows, preserve the fixture's theme/capital values and
mark both sources `sample_fixture`. For neutral and local-raw preparation,
retain the current unavailable behavior unless local-raw already provides
historical values under its own contract.

- [ ] **Step 3: Add deterministic positive raw files**

Create an offline date series in which one eligible selection date has:

```csv
theme_tags,theme_rank,same_theme_strong_count,main_net,big_order_net,theme_source,capital_source
AI算力,1,3,3000,2000,sample_fixture,sample_fixture
```

and the following date has OHLC values that complete an exit under the
unchanged `simulate_exit()` rules. Leave `tail_pullback_pct` blank.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b_preparation.py -q
```

Expected: all Phase 3.2b preparation tests pass.

### Task 3: Propagate Fixture Disclosure Without Altering Trading Rules

**Files:**
- Modify: `overnight_quant/backtest/data_preparation.py`
- Modify: `overnight_quant/backtest/backtest_report.py`
- Modify: `overnight_quant/tests/test_phase32b_preparation.py`

- [ ] **Step 1: Add report assertions first**

Assert manifest, prepare report, `data_quality.md`, and
`backtest_summary.md` include:

```text
positive profile uses deterministic sample_fixture theme/capital fields for pipeline validation
not live-filled
not strict historical
not evidence of strategy profitability
DAILY_PROXY only
```

- [ ] **Step 2: Verify RED**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b_preparation.py -q
```

Expected: report assertions fail until disclosure forwarding is implemented.

- [ ] **Step 3: Implement disclosure forwarding**

Store the positive disclosure lines in `dataset_manifest.yaml` and
`prepare_report_*.md`. In `backtest_report.py`, when the manifest indicates
`sample_profile: positive`, append the same source and limitation disclosure
to the existing DAILY_PROXY summary and quality reports. Do not change the
provider's candidate values or any transaction decision.

- [ ] **Step 4: Verify GREEN**

Run:

```text
python -m pytest overnight_quant/tests/test_phase32b_preparation.py -q
python -m pytest overnight_quant/tests/test_phase32_daily_proxy.py -q
```

Expected: both suites pass.

### Task 4: Document And Verify The Narrow Release

**Files:**
- Modify: `overnight_quant/README.md`
- Modify: `overnight_quant/RELEASE_NOTES.md`

- [ ] **Step 1: Document positive validation command**

Document the explicit `--sample-profile positive` command, neutral default,
and the fact that positive fixture fields are deterministic research inputs,
not live-filled or strict historical evidence.

- [ ] **Step 2: Run full verification**

Run:

```text
python -m pytest overnight_quant/tests -q
python overnight_quant/scripts/prepare_backtest_data.py --source sample --sample-profile positive --codes 300201 --start 2025-01-01 --end 2025-01-31 --out-dir overnight_quant/backtest_data/processed --overwrite
python overnight_quant/scripts/run_backtest.py --dataset local --fidelity daily_proxy --data-dir overnight_quant/backtest_data/processed
python overnight_quant/scripts/run_backtest.py --dataset sample --fidelity sample_exact
```

Expected: tests pass, positive daily proxy reports at least one trade, and
sample exact remains unchanged.

- [ ] **Step 3: Audit and commit only scoped files**

Run:

```text
git diff --check
git status --short --untracked-files=all
```

Stage only `overnight_quant/` source, docs, tests, and committed fixture
files. Do not stage generated processed data, backtest outputs, or unrelated
untracked files. Commit with a concise Phase 3.2b-1.5 message.
