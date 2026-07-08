# Phase 4.1 After-Close Watchlist Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only after-close workflow that generates a next-morning observation watchlist without producing tickets, recording orders, or automating trading.

**Architecture:** Add an independent `AfterCloseAnalyzer` that consumes the existing read-only `AStockClient` interface and writes through dedicated Markdown/CSV report helpers. The analyzer owns after-close session qualification and observation classification; it never invokes the tail-session scan strategy or execution modules. Existing `config_for_mode()` provides demo/example versus live/real path isolation.

**Tech Stack:** Python standard library (`argparse`, `csv`, `datetime`, `pathlib`, `collections`), existing `overnight_quant` data/calendar/config utilities, and `pytest`.

---

## Scope Lock And File Map

The implementation adds:

```text
overnight_quant/strategy/after_close_analysis.py
  Independent after-close gating, scoring, A/B/C classification, watch-plan
  templates, next-weekday date calculation, and data-quality flags.

overnight_quant/reports/after_close_report.py
  Markdown report and fixed-header watchlist CSV serialization only.

overnight_quant/scripts/run_after_close_analysis.py
  CLI wiring: mode/date input, path isolation, read-only client construction,
  analyzer invocation, report writing, and console summary.

overnight_quant/tests/test_phase41_after_close_analysis.py
  Session, fallback, classification, quality, output isolation, and
  no-execution regression tests.
```

The implementation modifies:

```text
overnight_quant/config.yaml
  Adds after_close category limits and thresholds.

overnight_quant/strategy/yang_yongxing_overnight.py
  Adds only Python fallback values for after_close configuration.

overnight_quant/README.md
  Documents the read-only after-close commands and output isolation.

overnight_quant/RELEASE_NOTES.md
  Records Phase 4.1 functionality and safety boundary.
```

It does not modify or import from the new pipeline:

```text
overnight_quant/scripts/run_scan.py
overnight_quant/scripts/run_sell_plan.py
overnight_quant/scripts/run_record_order.py
overnight_quant/scripts/run_trade_review.py
overnight_quant/scripts/run_backtest.py
overnight_quant/execution/manual_ticket.py
overnight_quant/execution/order_recorder.py
overnight_quant/execution/position_tracker.py
```

The implementation commit will contain only source, tests, configuration, and
documentation. Generated reports and CSVs remain runtime artifacts and are
not staged.

## Result Contract

The analyzer returns one dictionary suitable for both writers and CLI output:

```python
{
    "trade_date": "2026-05-28",
    "next_trade_date": "2026-05-29",
    "next_trade_date_calendar": "weekday_proxy",
    "mode": "demo",
    "status": "DEMO_ANALYSIS",
    "session_state": "AFTER_CLOSE",
    "candidate_source": "demo",
    "valid_for_trading_observation": "DEMO_ONLY",
    "market": {},
    "market_score": 85.0,
    "themes": [],
    "categories": {"A": [], "B": [], "C": []},
    "quality": {"source_status": [], "warnings": [], "fallback_to_demo": False},
    "report_path": "",
    "watchlist_csv": "",
}
```

Allowed status values are fixed:

```text
DEMO_ANALYSIS
NOT_TRADING_DAY
NOT_AFTER_CLOSE
DATA_FALLBACK_DEMO
DATA_QUALITY_BLOCKED
WATCHLIST_READY
NO_WATCHLIST
```

The writers serialize only the rows in `categories`. Blocked live states set
all category row lists to empty before writing.

### Task 1: Add The Gated Output Contract Tests

**Files:**
- Create: `overnight_quant/tests/test_phase41_after_close_analysis.py`
- Test: `overnight_quant/tests/test_phase41_after_close_analysis.py`

- [ ] **Step 1: Write failing path/session tests**

Create fixture helpers that copy `load_config()`, route real paths to a
temporary directory, and build a read-only fake client:

```python
class StubAfterCloseClient:
    def __init__(self, mode, rows, market=None, fallback=False):
        self.mode = mode
        self._rows = rows
        self._market = market or demo_market_snapshot()
        self.quality_report = SimpleNamespace(
            fallback_to_demo=fallback,
            source_status=[],
            warnings=[],
        )

    def get_market_snapshot(self):
        return dict(self._market)

    def get_candidate_quotes(self):
        return [dict(row) for row in self._rows]

    def get_daily_kline(self, code, lookback=120):
        return demo_daily_kline(code, lookback)
```

Write tests with the target public entry point:

```python
result = run_after_close_analysis(
    mode="live",
    now=datetime(2026, 5, 22, 10, 0, tzinfo=CN_TZ),
    config=config,
    client=client,
)
assert result["status"] == "NOT_AFTER_CLOSE"
assert result["valid_for_trading_observation"] == "NO"
assert _csv_rows(result["watchlist_csv"]) == []
```

Cover:

```text
demo output writes under examples and labels DEMO_ONLY
live weekend writes NOT_TRADING_DAY with header-only CSV
live pre-close writes NOT_AFTER_CLOSE with header-only CSV
```

- [ ] **Step 2: Run the test to verify RED**

Run:

```text
python -m pytest overnight_quant/tests/test_phase41_after_close_analysis.py -q
```

Expected result: import failure for
`overnight_quant.scripts.run_after_close_analysis` because the new entry point
does not yet exist.

### Task 2: Implement Minimal Session-Gated Analyzer And Writers

**Files:**
- Create: `overnight_quant/strategy/after_close_analysis.py`
- Create: `overnight_quant/reports/after_close_report.py`
- Create: `overnight_quant/scripts/run_after_close_analysis.py`
- Modify: `overnight_quant/config.yaml`
- Modify: `overnight_quant/strategy/yang_yongxing_overnight.py`
- Test: `overnight_quant/tests/test_phase41_after_close_analysis.py`

- [ ] **Step 1: Add configuration defaults**

Append to YAML and `DEFAULT_CONFIG`:

```yaml
after_close:
  max_a_count: 5
  max_b_count: 10
  max_c_count: 10
  min_a_score: 80
  min_b_score: 70
  min_c_score: 60
```

- [ ] **Step 2: Create report writers with a fixed CSV contract**

Define:

```python
WATCHLIST_FIELDS = [
    "trade_date", "next_trade_date", "code", "name", "category", "score",
    "theme_tags", "close_price", "change_pct", "turnover_pct", "amount_wan",
    "vol_ratio", "main_net", "main_net_source", "estimated_capital_flow",
    "reason", "risk_flags", "tomorrow_watch_plan", "invalid_conditions",
    "data_quality_flags",
]

def write_watchlist_csv(result: dict, records_dir: str) -> str: ...
def write_after_close_report(result: dict, reports_dir: str) -> str: ...
```

Use `csv.DictWriter` so blocked states naturally write a header-only file
when category rows are empty. Report metadata must include:

```text
date, mode, status, session_state, candidate_source,
valid_for_trading_observation, final_view,
next_trade_date_calendar: weekday_proxy
```

- [ ] **Step 3: Create the independent analyzer skeleton**

Define:

```python
class AfterCloseAnalyzer:
    def __init__(self, client, config: dict, mode: str, now: datetime | None = None):
        ...

    def analyze(self, trade_date: str | None = None) -> dict:
        ...
```

For the first passing slice, apply gates before candidate classification:

```python
if self.mode == "demo":
    status = "DEMO_ANALYSIS"
    validity = "DEMO_ONLY"
elif not is_likely_cn_trade_day(self.now):
    return self._blocked_result("NOT_TRADING_DAY")
elif get_session_state(self.now) != AFTER_CLOSE:
    return self._blocked_result("NOT_AFTER_CLOSE")
```

The analyzer imports only read-only calendar utilities; it must not import
`YangYongxingOvernightStrategy` or anything in `execution/`.

- [ ] **Step 4: Create CLI wiring**

Define:

```python
def run_after_close_analysis(
    mode: str = "demo",
    trade_date: str | None = None,
    config: dict | None = None,
    client=None,
    now: datetime | None = None,
) -> dict:
    runtime_config = config_for_mode(config or load_config(), mode)
    runtime_client = client or AStockClient(mode, now=now)
    result = AfterCloseAnalyzer(runtime_client, runtime_config, mode, now).analyze(trade_date)
    result["report_path"] = write_after_close_report(result, runtime_config["paths"]["reports_dir"])
    result["watchlist_csv"] = write_watchlist_csv(result, runtime_config["paths"]["records_dir"])
    return result
```

Parse `--mode demo|live` and `--date YYYY-MM-DD`. Print:

```text
Mode:
Status:
Session State:
Candidate Source:
Valid For Trading Observation:
A Count:
B Count:
C Count:
Report:
Watchlist CSV:
```

- [ ] **Step 5: Run gated tests to verify GREEN**

Run:

```text
python -m pytest overnight_quant/tests/test_phase41_after_close_analysis.py -q
```

Expected: the path/session tests pass, with empty rows for blocked live
states and example paths for demo.

### Task 3: Add Observation Classification And Watch-Plan Tests

**Files:**
- Modify: `overnight_quant/tests/test_phase41_after_close_analysis.py`
- Modify: `overnight_quant/strategy/after_close_analysis.py`

- [ ] **Step 1: Write failing classification tests**

Use deterministic copies of `demo_quotes()` and an after-close live timestamp:

```python
result = run_after_close_analysis(
    mode="live",
    now=datetime(2026, 5, 22, 15, 30, tzinfo=CN_TZ),
    config=config,
    client=StubAfterCloseClient("live", rows),
)
assert result["status"] == "WATCHLIST_READY"
assert result["categories"]["A"]
assert all(row["tomorrow_watch_plan"] for row in result["categories"]["A"])
assert all(row["invalid_conditions"] for row in result["categories"]["A"])
```

Add assertions that:

```text
known hard-risk candidates do not enter A/B
capital-outflow or limit-up chase candidates may appear only in C
C rows have an empty tomorrow_watch_plan and a do-not-chase reason
category counts remain within configured caps
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```text
python -m pytest overnight_quant/tests/test_phase41_after_close_analysis.py -q
```

Expected result: failures for missing category rows or missing observation
plan fields.

- [ ] **Step 3: Implement score and category calculation**

Implement functions local to `after_close_analysis.py`:

```python
def _market_score(market: dict) -> tuple[float, list[str]]: ...
def _score_candidate(stock: dict, kline: list[dict], market_score: float, config: dict) -> dict: ...
def _quality_flags(stock: dict) -> list[str]: ...
def _hard_exclusions(stock: dict, quality_flags: list[str], config: dict) -> list[str]: ...
def _watch_plan(stock: dict, category: str) -> tuple[str, str]: ...
```

Calculate the independent observation score:

```python
total_score = (
    market_score * 0.10
    + theme_score * 0.25
    + price_volume_score * 0.25
    + trend_score * 0.20
    + capital_score * 0.10
    + risk_score * 0.10
)
```

Use explicit output categories:

```python
if not hard_exclusions and score >= min_a:
    category = "A"
elif not hard_exclusions and score >= min_b:
    category = "B"
elif score >= min_c or _risk_observation_candidate(stock, hard_exclusions):
    category = "C"
```

For C:

```python
row["tomorrow_watch_plan"] = ""
row["invalid_conditions"] = "Risk observation only; do not chase; remove from view if weakness continues."
```

- [ ] **Step 4: Run classification tests to verify GREEN**

Run:

```text
python -m pytest overnight_quant/tests/test_phase41_after_close_analysis.py -q
```

Expected: category, cap, and watch-plan assertions pass.

### Task 4: Add Live Fallback And Quality-Blocking Tests

**Files:**
- Modify: `overnight_quant/tests/test_phase41_after_close_analysis.py`
- Modify: `overnight_quant/strategy/after_close_analysis.py`
- Modify: `overnight_quant/reports/after_close_report.py`

- [ ] **Step 1: Write failing quality tests**

Cover:

```python
fallback_client = StubAfterCloseClient("live", demo_quotes(), fallback=True)
result = run_after_close_analysis(
    mode="live",
    now=datetime(2026, 5, 22, 15, 30, tzinfo=CN_TZ),
    config=config,
    client=fallback_client,
)
assert result["status"] == "DATA_FALLBACK_DEMO"
assert result["candidate_source"] == "demo_fallback"
assert result["valid_for_trading_observation"] == "NO"
assert _csv_rows(result["watchlist_csv"]) == []
```

Add reliable-row modifications for:

```text
fund_flow_source=estimated_from_big_order_net -> estimated_capital_flow flag
theme_tags=[] -> theme_missing flag
main_net=None and big_order_net=None -> capital_missing flag
_freshness_reasons=["quote_stale"] -> DATA_QUALITY_BLOCKED
_risk_unknown_reasons=["st_status_unknown"] -> safety_field_unknown and DATA_QUALITY_BLOCKED
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```text
python -m pytest overnight_quant/tests/test_phase41_after_close_analysis.py -q
```

Expected: failures for unimplemented fallback/quality gate or flags.

- [ ] **Step 3: Implement candidate-source and quality gate behavior**

After live after-close candidate scoring:

```python
fallback = bool(getattr(self.client.quality_report, "fallback_to_demo", False))
if fallback:
    return self._blocked_with_context("DATA_FALLBACK_DEMO", "demo_fallback")

blocked_ab = [
    row for row in scored
    if row["would_be_a_or_b"] and
       set(row["data_quality_flags"]) & {"quote_stale", "freshness_unknown", "safety_field_unknown"}
]
if blocked_ab:
    return self._blocked_with_context("DATA_QUALITY_BLOCKED", "live")
```

The result report data-quality section must state any fallback and flags and,
when present, include:

```text
Capital-flow values marked estimated are auxiliary observation inputs only,
not confirmed capital inflow.
```

- [ ] **Step 4: Run quality tests to verify GREEN**

Run:

```text
python -m pytest overnight_quant/tests/test_phase41_after_close_analysis.py -q
```

Expected: fallback and quality-gate tests pass.

### Task 5: Add Isolation And No-Execution Regression Tests

**Files:**
- Modify: `overnight_quant/tests/test_phase41_after_close_analysis.py`
- Modify: `overnight_quant/reports/after_close_report.py`
- Modify: `overnight_quant/strategy/after_close_analysis.py`

- [ ] **Step 1: Write failing or enforcing isolation tests**

Add assertions:

```text
demo report and CSV paths contain /examples/
live report and CSV paths do not contain /examples/
no path matching manual_order_ticket_*.md is created
new production Python modules contain no forbidden order/click/broker/GUI token
report includes required Risk Warning
CSV header equals WATCHLIST_FIELDS exactly
next_trade_date_calendar equals weekday_proxy
```

- [ ] **Step 2: Run tests and address only demonstrated failures**

Run:

```text
python -m pytest overnight_quant/tests/test_phase41_after_close_analysis.py -q
```

Expected: failures, if any, identify missing disclosure or isolation output.
Add only the report/serialization text required to make these assertions pass.

- [ ] **Step 3: Re-run tests to verify GREEN**

Run:

```text
python -m pytest overnight_quant/tests/test_phase41_after_close_analysis.py -q
```

Expected: all Phase 4.1 tests pass.

### Task 6: Document The Read-Only Workflow

**Files:**
- Modify: `overnight_quant/README.md`
- Modify: `overnight_quant/RELEASE_NOTES.md`
- Test: `overnight_quant/tests/test_phase41_after_close_analysis.py`

- [ ] **Step 1: Add README operation section**

Document:

```text
python overnight_quant/scripts/run_after_close_analysis.py --mode demo
python overnight_quant/scripts/run_after_close_analysis.py --mode live
```

State that demo writes under `examples/`, live writes real-state research
outputs only after close, fallback/pre-close outputs are header-only, and the
command never creates tickets or submits orders.

- [ ] **Step 2: Add release note**

Add a Phase 4.1 entry stating:

```text
read-only after-close observation watchlist
conservative session/fallback/freshness gates
no manual tickets or trading integration
```

- [ ] **Step 3: Run Phase 4.1 tests**

Run:

```text
python -m pytest overnight_quant/tests/test_phase41_after_close_analysis.py -q
```

Expected: pass.

### Task 7: Verification And The Single Implementation Commit

**Files:**
- Stage only the modified Phase 4.1 source, test, configuration, and
  documentation files.

- [ ] **Step 1: Verify the complete test suite**

Run:

```text
python -m pytest overnight_quant/tests -q
```

Expected: all existing and newly added tests pass.

- [ ] **Step 2: Verify demo CLI behavior**

Run:

```text
python overnight_quant/scripts/run_after_close_analysis.py --mode demo
```

Expected console fields:

```text
Mode: demo
Status: DEMO_ANALYSIS
Valid For Trading Observation: DEMO_ONLY
Report: overnight_quant/examples/reports/after_close_analysis_<date>.md
Watchlist CSV: overnight_quant/examples/records/next_morning_watchlist_<date>.csv
```

- [ ] **Step 3: Verify live gate behavior according to current session**

Run:

```text
python overnight_quant/scripts/run_after_close_analysis.py --mode live
```

If the current live session is before close, expect:

```text
Status: NOT_AFTER_CLOSE
Valid For Trading Observation: NO
```

If it is after close, inspect whether data returns `WATCHLIST_READY`,
`NO_WATCHLIST`, `DATA_FALLBACK_DEMO`, or `DATA_QUALITY_BLOCKED` and confirm
the corresponding CSV-row rule.

- [ ] **Step 4: Verify prohibited execution code is absent**

Run:

```text
rg -n -i "pyautogui|selenium|broker api|auto_order|place_order|automatic order|automatic click|自动下单|自动点击" overnight_quant/strategy/after_close_analysis.py overnight_quant/reports/after_close_report.py overnight_quant/scripts/run_after_close_analysis.py
```

Expected: no production-code match.

- [ ] **Step 5: Review staging boundary and commit**

Run only exact staging:

```text
git add -- overnight_quant/strategy/after_close_analysis.py overnight_quant/reports/after_close_report.py overnight_quant/scripts/run_after_close_analysis.py overnight_quant/tests/test_phase41_after_close_analysis.py overnight_quant/config.yaml overnight_quant/strategy/yang_yongxing_overnight.py overnight_quant/README.md overnight_quant/RELEASE_NOTES.md
git diff --cached --name-only
git commit -m "Add after-close next-morning watchlist"
```

Do not stage `AGENTS.md`, generated `records/`, generated `reports/`,
`examples/` runtime files, cache, or backtest outputs.

## Commit Contract

The plan document is committed separately before implementation:

```text
git add -- overnight_quant/docs/phase4_1_after_close_watchlist_implementation_plan.md
git commit -m "Plan after-close watchlist analysis"
```

Although the TDD cycle proceeds through multiple red/green checkpoints, the
user-requested implementation boundary is one verified implementation commit:

```text
git commit -m "Add after-close next-morning watchlist"
```
