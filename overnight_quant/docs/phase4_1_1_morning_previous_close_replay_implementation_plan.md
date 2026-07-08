# Phase 4.1.1 Morning Previous-Close Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit live pre-market catch-up mode that reconstructs today's observation watchlist only from precisely dated prior-close data.

**Architecture:** Extend the independent Phase 4.1 observation pipeline rather than the transaction scan path. `run_after_close_analysis.py --mode live --replay-previous-close` supplies a strict replay context to `AStockClient` and `AfterCloseAnalyzer`; the client validates all replay-sensitive timestamps against the prior likely weekday, while dedicated writer branches produce non-overwriting `morning_replay_*` artifacts. Ordinary after-close and live scan freshness behavior stays unchanged.

**Tech Stack:** Python standard library (`argparse`, `datetime`, `csv`, `pathlib`), existing `AStockClient`/quality/calendar/after-close modules, YAML configuration already in the package, and `pytest`.

---

## Scope Lock And File Map

Create:

```text
overnight_quant/tests/test_phase411_morning_previous_close_replay.py
```

Modify:

```text
overnight_quant/data/market_calendar.py
  Add the weekday-proxy prior-session resolver used only for replay lineage.

overnight_quant/data/astock_client.py
  Add an explicit data context and expected-date validation; constrain THS
  replay seeds, Tencent dated quote use, Baidu dated K-line use, and replay
  fund flow to prior-day-only sources.

overnight_quant/data/live_data_quality.py
  Preserve expected-date/freshness-basis and mismatch counters for audit
  rendering when the client is in replay context.

overnight_quant/strategy/after_close_analysis.py
  Add replay analysis mode, replay session gate/statuses, and result lineage.
  Keep the Phase 4.1 score formula, category limits, and watch templates.

overnight_quant/reports/after_close_report.py
  Serialize isolated morning replay filenames, lineage metadata, and extended
  CSV headers without changing ordinary after-close filenames/headers.

overnight_quant/scripts/run_after_close_analysis.py
  Add the opt-in CLI flag and inject a strict live replay client context.

overnight_quant/README.md
overnight_quant/RELEASE_NOTES.md
  Document the replay-only observation workflow and its safety limits.
```

Do not modify:

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

Do not stage generated output beneath `records/`, `reports/`, `examples/`,
cache, or `backtest_outputs`; do not stage `AGENTS.md`.

## Contract Decisions

### Replay Invocation

```text
python overnight_quant/scripts/run_after_close_analysis.py \
  --mode live \
  --replay-previous-close
```

`--replay-previous-close` is invalid with demo:

```text
REPLAY_REQUIRES_LIVE_MODE
```

The first implementation does not support manually backdating a live replay.
If replay is passed with `--date`, reject it clearly:

```text
REPLAY_DATE_OVERRIDE_UNSUPPORTED
```

This keeps `observation_date` bound to the actual run date.

### Replay Lineage

For a run at `2026-05-28 08:45`:

```python
{
    "analysis_mode": "previous_close_replay",
    "observation_date": "2026-05-28",
    "trade_date": "2026-05-27",
    "next_trade_date": "2026-05-28",
    "replay_as_of_date": "2026-05-27",
    "replay_calendar": "weekday_proxy",
    "candidate_source": "live_previous_close_replay",
    "freshness_basis": "previous_close_expected",
}
```

### Status Contract

```text
MORNING_REPLAY_READY
MORNING_REPLAY_NO_WATCHLIST
NOT_REPLAY_WINDOW
REPLAY_DATA_FALLBACK_DEMO
REPLAY_DATA_QUALITY_BLOCKED
```

Only `MORNING_REPLAY_READY` can serialize A/B/C rows. The replay path accepts
`PRE_MARKET` and `CALL_AUCTION`; it rejects continuous trading, afternoon,
after-close, and non-trading days because it is specifically a missed
prior-close recovery command.

### Strict Freshness Contract

```python
DATA_CONTEXT_CURRENT = "current_live"
DATA_CONTEXT_PREVIOUS_CLOSE_REPLAY = "previous_close_replay"
```

Under `previous_close_replay`, every replay-sensitive source is checked against
`expected_data_date`:

```text
equal date       -> freshness_basis=previous_close_expected, usable
older date       -> replay_data_too_old + freshness_unknown, blocked
newer date       -> replay_data_from_observation_day + freshness_unknown, blocked
missing date     -> timestamp_missing + freshness_unknown, blocked
fallback to demo -> REPLAY_DATA_FALLBACK_DEMO, blocked
```

Ordinary live freshness continues comparing against today's date and retains
its existing `quote_stale` behavior.

## Task 1: Add Replay Time, Output, And CLI Contract Tests

**Files:**
- Create: `overnight_quant/tests/test_phase411_morning_previous_close_replay.py`
- Modify: `overnight_quant/data/market_calendar.py`
- Modify: `overnight_quant/scripts/run_after_close_analysis.py`
- Modify: `overnight_quant/reports/after_close_report.py`

- [ ] **Step 1: Write failing calendar and replay output tests**

Create a focused test module that imports the existing Phase 4.1 stub idea and
introduces a replay-dated client:

```python
class ReplayStubClient:
    def __init__(self, rows, fallback=False):
        self.rows = copy.deepcopy(rows)
        self.fallback_messages = ["live data failed, fallback to demo"] if fallback else []
        self.quality_report = SimpleNamespace(
            fallback_to_demo=fallback,
            source_status=[],
            warnings=list(self.fallback_messages),
            freshness_summary={"fresh": 1, "stale": 0, "unknown": 0},
            stale_sources=[],
        )

    def get_market_snapshot(self):
        return demo_market_snapshot()

    def get_candidate_quotes(self):
        return copy.deepcopy(self.rows)

    def get_daily_kline(self, code, lookback=120):
        return demo_daily_kline(code, lookback)

    def get_kline_freshness_reasons(self, code):
        return []
```

Add tests:

```python
def test_previous_likely_trade_day_uses_weekday_proxy():
    assert previous_likely_cn_trade_day(date(2026, 5, 28)).isoformat() == "2026-05-27"
    assert previous_likely_cn_trade_day(date(2026, 5, 25)).isoformat() == "2026-05-22"

def test_morning_replay_writes_distinct_lineage_output_paths(tmp_path):
    result = run_after_close_analysis(
        mode="live",
        replay_previous_close=True,
        now=datetime(2026, 5, 28, 8, 45, tzinfo=CN_TZ),
        config=_tmp_config(tmp_path),
        client=ReplayStubClient([demo_quotes()[0]]),
    )
    assert result["analysis_mode"] == "previous_close_replay"
    assert result["observation_date"] == "2026-05-28"
    assert result["replay_as_of_date"] == "2026-05-27"
    assert Path(result["report_path"]).name == "morning_replay_analysis_2026-05-28.md"
    assert Path(result["watchlist_csv"]).name == "morning_replay_watchlist_2026-05-28.csv"
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```text
python -m pytest overnight_quant/tests/test_phase411_morning_previous_close_replay.py -q
```

Expected: import or argument failures because the prior-session helper and
replay entry-point contract do not yet exist.

- [ ] **Step 3: Implement minimal weekday resolver and writer branching**

Add to `market_calendar.py`:

```python
def previous_likely_cn_trade_day(value: date | datetime) -> date:
    current = value.date() if isinstance(value, datetime) else value
    current -= timedelta(days=1)
    while not is_likely_cn_trade_day(current):
        current -= timedelta(days=1)
    return current
```

Add `replay_previous_close: bool = False` to
`run_after_close_analysis()`, pass `analysis_mode="previous_close_replay"` to
the analyzer, and make writer filenames branch on `result["analysis_mode"]`:

```python
if result.get("analysis_mode") == "previous_close_replay":
    output = path / f"morning_replay_analysis_{result['observation_date']}.md"
else:
    output = path / f"after_close_analysis_{result['trade_date']}.md"
```

The corresponding CSV branch must use
`morning_replay_watchlist_<observation_date>.csv` and its extended lineage
header without changing `WATCHLIST_FIELDS` for Phase 4.1 outputs.

- [ ] **Step 4: Run the focused tests to verify GREEN for this contract**

Run:

```text
python -m pytest overnight_quant/tests/test_phase411_morning_previous_close_replay.py -q
```

Expected: date/output contract tests pass; later behavior tests may remain to
be added in subsequent tasks.

## Task 2: Add Replay Window And Analyzer Status Behavior

**Files:**
- Modify: `overnight_quant/tests/test_phase411_morning_previous_close_replay.py`
- Modify: `overnight_quant/strategy/after_close_analysis.py`
- Modify: `overnight_quant/reports/after_close_report.py`

- [ ] **Step 1: Write failing replay gate/status tests**

Add:

```python
def test_replay_pre_market_can_produce_formal_rows(tmp_path):
    result = _run_replay(tmp_path, datetime(2026, 5, 28, 8, 45, tzinfo=CN_TZ))
    assert result["status"] == "MORNING_REPLAY_READY"
    assert result["candidate_source"] == "live_previous_close_replay"
    assert result["freshness_basis"] == "previous_close_expected"
    assert _csv_rows(result["watchlist_csv"])

def test_replay_call_auction_can_produce_formal_rows(tmp_path):
    assert _run_replay(tmp_path, datetime(2026, 5, 28, 9, 20, tzinfo=CN_TZ))["status"] == "MORNING_REPLAY_READY"

@pytest.mark.parametrize("now", [
    datetime(2026, 5, 23, 8, 45, tzinfo=CN_TZ),
    datetime(2026, 5, 28, 9, 31, tzinfo=CN_TZ),
    datetime(2026, 5, 28, 15, 30, tzinfo=CN_TZ),
])
def test_replay_outside_window_writes_header_only(tmp_path, now):
    result = _run_replay(tmp_path, now)
    assert result["status"] == "NOT_REPLAY_WINDOW"
    assert result["valid_for_trading_observation"] == "NO"
    assert _csv_rows(result["watchlist_csv"]) == []
```

- [ ] **Step 2: Run gate tests to verify RED**

Run:

```text
python -m pytest overnight_quant/tests/test_phase411_morning_previous_close_replay.py -q
```

Expected: status or row failures because the analyzer still applies the
ordinary `NOT_AFTER_CLOSE` gate.

- [ ] **Step 3: Implement replay mode in `AfterCloseAnalyzer`**

Extend constructor:

```python
def __init__(self, client, config, mode, now=None, analysis_mode="after_close"):
    self.analysis_mode = analysis_mode
```

Build replay lineage in `_base_result()`:

```python
if self.analysis_mode == "previous_close_replay":
    observation_date = self.now.date().isoformat()
    replay_date = previous_likely_cn_trade_day(self.now).isoformat()
    result.update({
        "analysis_mode": "previous_close_replay",
        "observation_date": observation_date,
        "replay_as_of_date": replay_date,
        "replay_calendar": "weekday_proxy",
        "trade_date": replay_date,
        "next_trade_date": observation_date,
        "candidate_source": "live_previous_close_replay",
        "freshness_basis": "previous_close_expected",
    })
```

Before calling client data in replay mode:

```python
if not is_likely_cn_trade_day(self.now) or result["session_state"] not in {PRE_MARKET, CALL_AUCTION}:
    return self._blocked_result(result, "NOT_REPLAY_WINDOW")
```

Map fallback and quality outcomes to replay-specific statuses:

```python
fallback_status = "REPLAY_DATA_FALLBACK_DEMO" if replay else "DATA_FALLBACK_DEMO"
quality_status = "REPLAY_DATA_QUALITY_BLOCKED" if replay else "DATA_QUALITY_BLOCKED"
ready_status = "MORNING_REPLAY_READY" if replay else "WATCHLIST_READY"
empty_status = "MORNING_REPLAY_NO_WATCHLIST" if replay else "NO_WATCHLIST"
```

- [ ] **Step 4: Update report metadata and header-only status set**

For replay reports render:

```text
analysis_mode: previous_close_replay
observation_date:
replay_as_of_date:
replay_calendar: weekday_proxy
freshness_basis:
```

Include replay blocked statuses in the header-only/no-formal-row rendering
set. Add the mandatory replay warning text specified in the design.

- [ ] **Step 5: Run replay and existing Phase 4.1 tests**

Run:

```text
python -m pytest overnight_quant/tests/test_phase411_morning_previous_close_replay.py overnight_quant/tests/test_phase41_after_close_analysis.py -q
```

Expected: replay gates pass and original Phase 4.1 results remain green.

## Task 3: Implement Strict Expected-Date Read-Only Data Context

**Files:**
- Modify: `overnight_quant/tests/test_phase411_morning_previous_close_replay.py`
- Modify: `overnight_quant/data/astock_client.py`
- Modify: `overnight_quant/data/live_data_quality.py`

- [ ] **Step 1: Write failing strict-date client tests**

Use a `ReplaySourceClient(AStockClient)` test double that overrides network
methods while exercising production merge/freshness logic:

```python
client = ReplaySourceClient(
    mode="live",
    now=datetime(2026, 5, 28, 8, 45, tzinfo=CN_TZ),
    data_context="previous_close_replay",
    expected_data_date="2026-05-27",
    quote_date="2026-05-27",
    kline_date="2026-05-27",
)
rows = client.get_candidate_quotes()
assert "quote_stale" not in rows[0]["_freshness_reasons"]
assert rows[0]["_freshness"]["tencent_quote"]["freshness_basis"] == "previous_close_expected"
```

Add failures for:

```text
quote date 2026-05-26 -> freshness_unknown + replay_data_too_old
quote date 2026-05-28 -> freshness_unknown + replay_data_from_observation_day
quote timestamp absent -> timestamp_missing + freshness_unknown
kline last date mismatch -> replay analyzer blocks otherwise selectable row
THS replay seed method queried only once for expected date
replay fund path does not invoke minute fund flow
```

- [ ] **Step 2: Run strict-date tests to verify RED**

Run:

```text
python -m pytest overnight_quant/tests/test_phase411_morning_previous_close_replay.py -q
```

Expected: constructor/context or freshness failures before production changes.

- [ ] **Step 3: Add explicit client context and expected-date freshness evaluation**

Extend constructor with defaults so existing call sites remain unchanged:

```python
def __init__(..., data_context: str = "current_live", expected_data_date: str | None = None):
    self.data_context = data_context
    self.expected_data_date = expected_data_date
```

Centralize freshness evaluation:

```python
def _freshness_for_date(self, source, data_date, data_time="", stale_reason_if_today=""):
    if not data_date:
        return {..., "stale_reason": "timestamp_missing", "freshness_basis": "blocked"}
    if self.data_context == "previous_close_replay":
        if data_date == self.expected_data_date:
            return {..., "is_stale": False, "stale_reason": "", "freshness_basis": "previous_close_expected"}
        reason = "replay_data_too_old" if data_date < self.expected_data_date else "replay_data_from_observation_day"
        return {..., "is_stale": True, "stale_reason": reason, "freshness_basis": "blocked"}
    return existing_current_live_behavior
```

Update `_derive_freshness()` to use this context rather than overwriting a
correct replay-date item by comparing it to today's date. Update rejection
reason mapping so replay date mismatches include:

```text
freshness_unknown
replay_data_too_old | replay_data_from_observation_day
```

- [ ] **Step 4: Enforce target-date source behavior**

In `_ths_hot_reason_candidates()`:

```python
if self.data_context == "previous_close_replay":
    query_dates = [self.expected_data_date]
else:
    query_dates = [(self.now.date() - timedelta(days=offset)).isoformat() for offset in range(12)]
```

For replay fund flow, do not call `_eastmoney_fund_flow_minute()`. Introduce:

```python
def _safe_replay_fund_flow(self, code):
    rows = self._eastmoney_fund_flow_daily(code)
    dated = [row for row in rows if str(row.get("time", "")).startswith(self.expected_data_date)]
    return (dated, "eastmoney_fund_flow_daily", "") if dated else ([], "missing", "replay_daily_fund_flow_unavailable")
```

Select that method from `_build_live_candidates()` only in replay context.
If unavailable, `_merge_fund_flow()` may retain the existing target-day THS
`big_order_net` estimate and report `estimated_from_big_order_net`.

For replay market snapshot, avoid current minute northbound; use only dated
Tencent index rows that satisfy expected-date freshness, and report
unavailable optional market context rather than substituting demo/current
flow values.

- [ ] **Step 5: Preserve audit fields**

Extend quality extraction/reporting to include:

```text
expected_data_date
freshness_basis
target_date_match_count
target_date_mismatch_count
timestamp_missing_count
```

Increment counters when sources are recorded so the morning report explains
why rows were accepted or blocked.

- [ ] **Step 6: Run strict replay and existing live data tests**

Run:

```text
python -m pytest overnight_quant/tests/test_phase411_morning_previous_close_replay.py overnight_quant/tests/test_live_data_quality.py overnight_quant/tests/test_live_session_gate.py overnight_quant/tests/test_live_field_coverage.py -q
```

Expected: replay target-date tests pass while ordinary live current-day
freshness tests continue to pass.

## Task 4: Add CLI Errors, Documentation, And End-To-End Regression Proof

**Files:**
- Modify: `overnight_quant/tests/test_phase411_morning_previous_close_replay.py`
- Modify: `overnight_quant/scripts/run_after_close_analysis.py`
- Modify: `overnight_quant/README.md`
- Modify: `overnight_quant/RELEASE_NOTES.md`

- [ ] **Step 1: Write failing CLI and safety-boundary tests**

Add CLI tests using `monkeypatch` and `capsys`:

```python
def test_replay_requires_live_mode(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["run_after_close_analysis.py", "--mode", "demo", "--replay-previous-close"])
    assert main() == 2
    assert "REPLAY_REQUIRES_LIVE_MODE" in capsys.readouterr().out

def test_replay_rejects_date_override(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["run_after_close_analysis.py", "--mode", "live", "--replay-previous-close", "--date", "2026-05-27"])
    assert main() == 2
    assert "REPLAY_DATE_OVERRIDE_UNSUPPORTED" in capsys.readouterr().out
```

Assert that replay output does not create any
`manual_order_ticket_*.md`, and that modified production files do not contain
automatic order/click/broker integration tokens.

- [ ] **Step 2: Run CLI tests to verify RED**

Run:

```text
python -m pytest overnight_quant/tests/test_phase411_morning_previous_close_replay.py -q
```

Expected: CLI validation failures before flag parsing and errors are added.

- [ ] **Step 3: Add CLI flag, validation, and context construction**

Extend parser:

```python
parser.add_argument("--replay-previous-close", action="store_true")
```

Before invoking production functions:

```python
if args.replay_previous_close and args.mode != "live":
    print("REPLAY_REQUIRES_LIVE_MODE")
    return 2
if args.replay_previous_close and args.date:
    print("REPLAY_DATE_OVERRIDE_UNSUPPORTED")
    return 2
```

When no injected client is provided, instantiate:

```python
expected_date = previous_likely_cn_trade_day(now or datetime.now(CN_TZ)).isoformat()
AStockClient(
    mode,
    now=now,
    data_context="previous_close_replay",
    expected_data_date=expected_date,
)
```

Print replay lineage fields only in replay mode.

- [ ] **Step 4: Document operation and release boundary**

README must state:

```text
python overnight_quant/scripts/run_after_close_analysis.py --mode live --replay-previous-close
```

and explain:

- use only in `PRE_MARKET` or `CALL_AUCTION` when the prior evening report is
  missing;
- accepted rows are reconstructed from the exact prior close and are not
  current intraday confirmation;
- no ticket, automatic order, or click is generated.

Release notes add Phase 4.1.1 with strict prior-date replay and separate
artifact naming.

- [ ] **Step 5: Verify the focused and complete suites**

Run:

```text
python -m pytest overnight_quant/tests/test_phase411_morning_previous_close_replay.py -q
python -m pytest overnight_quant/tests -q
```

Expected: all tests pass.

- [ ] **Step 6: Verify CLI behavior**

Run:

```text
python overnight_quant/scripts/run_after_close_analysis.py --mode live --replay-previous-close
```

In `PRE_MARKET` or `CALL_AUCTION`, inspect whether the actual data is:

```text
MORNING_REPLAY_READY
MORNING_REPLAY_NO_WATCHLIST
REPLAY_DATA_FALLBACK_DEMO
REPLAY_DATA_QUALITY_BLOCKED
```

and confirm its report/CSV row contract. Outside the replay window, expect
`NOT_REPLAY_WINDOW` with a header-only CSV.

- [ ] **Step 7: Verify prohibited execution code and staging boundary**

Run:

```text
rg -n -i "pyautogui|selenium|broker api|auto_order|place_order|自动下单|自动点击|manual_ticket|order_recorder|position_tracker|YangYongxingOvernightStrategy" overnight_quant/data/astock_client.py overnight_quant/strategy/after_close_analysis.py overnight_quant/reports/after_close_report.py overnight_quant/scripts/run_after_close_analysis.py
git status --short --untracked-files=all
git diff --stat
```

Any existing token match must be reviewed as a prohibition statement only,
not executable integration. Do not stage runtime outputs or `AGENTS.md`.

## Commit Sequence

Before implementation, commit this plan alone:

```text
git add -- overnight_quant/docs/phase4_1_1_morning_previous_close_replay_implementation_plan.md
git commit -m "Plan morning previous-close replay watchlist flow"
```

After all TDD cycles and verification, stage only changed Phase 4.1.1 source,
test, and documentation files:

```text
git add -- \
  overnight_quant/data/market_calendar.py \
  overnight_quant/data/astock_client.py \
  overnight_quant/data/live_data_quality.py \
  overnight_quant/strategy/after_close_analysis.py \
  overnight_quant/reports/after_close_report.py \
  overnight_quant/scripts/run_after_close_analysis.py \
  overnight_quant/tests/test_phase411_morning_previous_close_replay.py \
  overnight_quant/README.md \
  overnight_quant/RELEASE_NOTES.md
git commit -m "Add morning previous-close replay watchlist"
```

If a listed file remains unchanged, omit it from `git add`. Never stage:

```text
AGENTS.md
overnight_quant/records/
overnight_quant/reports/*.md
overnight_quant/examples/records/
overnight_quant/examples/reports/
overnight_quant/backtest_outputs/
overnight_quant/backtest_data/cache/
```
