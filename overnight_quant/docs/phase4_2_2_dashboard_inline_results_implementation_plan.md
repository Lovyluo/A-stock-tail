# Phase 4.2.2 Dashboard Inline Results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the local dashboard show parsed observation results directly in the web UI, while keeping reports and records as hidden audit artifacts.

**Architecture:** Keep the dashboard as a UI-only layer. Reuse `result_parser.py` and the existing `load_dashboard_state()` file readers, then add presentation helpers in `dashboard.py` that convert parsed reports/tables into visible key metrics, concise summaries, and table previews. File paths remain available only inside audit/advanced expanders.

**Tech Stack:** Python, Streamlit, existing markdown/CSV parsers, pytest.

---

## Files

- Modify: `overnight_quant/ui/dashboard.py`
  - Add inline result summary helpers.
  - Move output directory/path display into audit expanders.
  - Render direct result cards and tables for preflight, live dry-run, after-close, morning replay, sell plan, lifecycle, and review.
- Modify: `overnight_quant/tests/test_phase42_dashboard.py`
  - Add tests for inline summaries, audit separation, table counts, and no new execution paths.
- Modify: `overnight_quant/README.md`
  - Note that the dashboard shows latest results directly and keeps files as audit records.
- Modify: `overnight_quant/RELEASE_NOTES.md`
  - Add Phase 4.2.2 summary.

## Task 1: Add Failing Dashboard Summary Tests

- [ ] **Step 1: Add tests**

Add these tests to `overnight_quant/tests/test_phase42_dashboard.py`:

```python
def test_inline_result_sections_show_values_without_paths():
    from overnight_quant.ui.dashboard import build_inline_result_sections

    state = {
        "preflight": {"status": "READY_FOR_LIVE_SCAN", "session_state": "PRE_MARKET", "path": "reports/preflight.md"},
        "dry_run": {
            "candidate_source": "live",
            "final_advice": "NO_TRADE",
            "valid_for_trading_observation": "NO",
            "path": "reports/dry_run.md",
        },
        "after_close": {"status": "WATCHLIST_READY", "path": "reports/after_close.md"},
        "morning_replay": {"status": "MORNING_REPLAY_READY", "path": "reports/replay.md"},
        "sell_plan": {"status": "NO_OPEN_POSITION", "path": "reports/sell_plan.md"},
        "lifecycle": {"status": "MISSING", "path": "reports/lifecycle.md"},
        "trade_review": {"status": "MISSING", "path": "reports/review.md"},
    }

    sections = build_inline_result_sections(state, "en")

    assert sections["preflight"][0] == ("Status", "READY_FOR_LIVE_SCAN")
    assert ("Session", "PRE_MARKET") in sections["preflight"]
    assert ("Candidate Source", "live") in sections["dry_run"]
    assert "reports/preflight.md" not in str(sections)
```

```python
def test_audit_file_rows_keep_paths_separate():
    from overnight_quant.ui.dashboard import audit_file_rows

    rows = audit_file_rows({"preflight": {"path": "reports/preflight.md"}, "dry_run": {"path": "reports/dry_run.md"}}, "en")

    assert ("Preflight", "reports/preflight.md") in rows
    assert ("Live Dry-run", "reports/dry_run.md") in rows
```

```python
def test_table_result_summary_reports_row_counts(tmp_path):
    from overnight_quant.ui.dashboard import table_result_summary
    from overnight_quant.ui.result_parser import parse_watchlist_csv

    csv_path = tmp_path / "watchlist.csv"
    csv_path.write_text("code,name,category\n300001,Demo,A\n300002,Demo2,B\n", encoding="utf-8")
    table = parse_watchlist_csv(csv_path)

    assert table_result_summary(table, "en") == "2 rows"
    assert table_result_summary(table, "zh") == "2 行"
```

```python
def test_dashboard_output_directories_are_audit_only_copy():
    from overnight_quant.ui.dashboard import t

    assert t("en", "audit_artifacts") == "Audit Artifacts"
    assert "审计" in t("zh", "audit_artifacts")
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python -m pytest overnight_quant/tests/test_phase42_dashboard.py -q
```

Expected: fail because `build_inline_result_sections`, `audit_file_rows`, `table_result_summary`, and `audit_artifacts` copy do not exist yet.

## Task 2: Implement Inline Result Helpers

- [ ] **Step 1: Add helper functions**

In `overnight_quant/ui/dashboard.py`, add:

```python
def table_result_summary(table, language: str) -> str: ...
def build_inline_result_sections(state: dict[str, Any], language: str) -> dict[str, list[tuple[str, str]]]: ...
def audit_file_rows(state: dict[str, Any], language: str) -> list[tuple[str, str]]: ...
```

These helpers must:

- Exclude `path` and `report_type` from visible summaries.
- Prefer stable labels for status, session, candidate source, final advice, validity, market gate, and status-like fields.
- Return table row counts without requiring pandas.
- Keep audit paths separate from main display.

- [ ] **Step 2: Run dashboard tests and verify GREEN**

Run:

```bash
python -m pytest overnight_quant/tests/test_phase42_dashboard.py -q
```

Expected: all dashboard tests pass.

## Task 3: Render Results Directly In Streamlit

- [ ] **Step 1: Replace path-first section rendering**

Update `_render_report_section()` so it:

- Shows a status card with a concise result value.
- Shows key parsed fields in the page body.
- Moves raw JSON and file path into an expander named `Audit Details`.

- [ ] **Step 2: Improve table rendering**

Update `render_table_or_empty()` so it:

- Shows a row count above non-empty tables.
- Keeps empty table messaging concise.
- Does not print CSV paths as the primary output.

- [ ] **Step 3: Move output directories into audit UI**

In `main()`, replace always-visible sidebar `reports:` / `records:` code block with an expander named `Audit Artifacts`.

- [ ] **Step 4: Run dashboard tests**

Run:

```bash
python -m pytest overnight_quant/tests/test_phase42_dashboard.py -q
```

Expected: pass.

## Task 4: Documentation And Verification

- [ ] **Step 1: Update docs**

Update `overnight_quant/README.md` and `overnight_quant/RELEASE_NOTES.md` to say:

- The dashboard now displays parsed results directly.
- Reports/records are retained as audit artifacts.
- Trading remains fully manual.

- [ ] **Step 2: Run full tests**

Run:

```bash
python -m pytest overnight_quant/tests -q
```

Expected: all tests pass.

- [ ] **Step 3: Browser smoke test**

Start the Streamlit dashboard in the background, open `http://localhost:8501`, and verify:

- Results are visible directly in cards/tables.
- Audit file paths are hidden in expanders.
- Language switch still works.
- No manual ticket/order execution buttons were added.

- [ ] **Step 4: Commit only Phase 4.2.2 files**

Stage exactly:

```bash
git add -- \
  overnight_quant/docs/phase4_2_2_dashboard_inline_results_implementation_plan.md \
  overnight_quant/ui/dashboard.py \
  overnight_quant/tests/test_phase42_dashboard.py \
  overnight_quant/README.md \
  overnight_quant/RELEASE_NOTES.md
```

Commit:

```bash
git commit -m "Show dashboard results inline with audit artifacts"
```

Do not stage `AGENTS.md`, `overnight_quant_实盘使用手册.md`, reports, records, examples, cache, or backtest outputs.

## Self-Review

- The plan keeps scope limited to dashboard presentation.
- It preserves file-based audit artifacts and all existing parsers.
- It does not add trading, ticket, order recording, broker API, or browser-click automation.
- It uses TDD with explicit failing tests before implementation.
