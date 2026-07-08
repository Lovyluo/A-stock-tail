# Phase 4.2 Local Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local Streamlit dashboard that reads existing reports, runs approved dry-run/analysis commands, and displays live-observation state without adding any trading execution path.

**Architecture:** Keep the UI as an optional, read-only layer under `overnight_quant/ui/`. `result_parser.py` parses existing markdown/CSV artifacts with graceful missing-file handling; `dashboard.py` renders tabs and uses a fixed command whitelist; `run_dashboard.py` only starts Streamlit and reports a clear dependency error if Streamlit is missing.

**Tech Stack:** Python standard library (`argparse`, `csv`, `subprocess`, `pathlib`, `sys`), optional `streamlit>=1.30`, optional `pandas>=2.0`, and existing report/record files.

---

## File Map

Create:

```text
requirements-ui.txt
overnight_quant/ui/__init__.py
overnight_quant/ui/result_parser.py
overnight_quant/ui/dashboard.py
overnight_quant/scripts/run_dashboard.py
overnight_quant/tests/test_phase42_dashboard.py
```

Modify:

```text
overnight_quant/README.md
overnight_quant/RELEASE_NOTES.md
```

Do not modify strategy, scan, sell, order-recorder, position-tracker, backtest, or execution modules. Do not stage generated records/reports/cache/backtest outputs or task-external files.

## Task 1: Write Dashboard Contract Tests

**Files:**
- Create: `overnight_quant/tests/test_phase42_dashboard.py`

- [ ] **Step 1: Add parser tests**

Add tests for:

```python
def test_parse_key_value_md_reads_top_level_fields(tmp_path): ...
def test_parse_missing_report_returns_clear_status(tmp_path): ...
def test_empty_watchlist_csv_loads_with_header(tmp_path): ...
def test_parse_dry_run_candidate_source(tmp_path): ...
```

The tests must assert missing files return a dictionary with `status="MISSING"` and that empty-header CSVs produce an empty table object with column names.

- [ ] **Step 2: Add command safety tests**

Add tests for:

```python
def test_command_whitelist_contains_only_approved_actions(): ...
def test_command_runner_uses_shell_false(monkeypatch): ...
def test_dashboard_does_not_expose_formal_live_scan_command(): ...
def test_dashboard_modules_do_not_import_execution_modules(): ...
```

Approved actions are only:

```text
preflight
live_dry_run
after_close_live
morning_replay_live
sell_plan_live
demo_after_close
demo_scan
```

No command may include `run_scan.py --mode live` unless `--dry-run` is present.

- [ ] **Step 3: Run RED**

Run:

```text
python -m pytest overnight_quant/tests/test_phase42_dashboard.py -q
```

Expected: import failures because `overnight_quant.ui` and `run_dashboard.py` do not exist yet.

## Task 2: Implement Result Parser

**Files:**
- Create: `overnight_quant/ui/__init__.py`
- Create: `overnight_quant/ui/result_parser.py`

- [ ] **Step 1: Implement file discovery and key-value parsing**

Create:

```python
def find_latest_file(pattern: str, directory: Path) -> Path | None: ...
def parse_key_value_md(path: Path) -> dict: ...
```

`parse_key_value_md` reads simple `key: value` lines until markdown sections, returns `{"status": "MISSING", "path": str(path)}` if the file is absent, and never raises for missing files.

- [ ] **Step 2: Implement report-specific wrappers**

Create:

```python
def parse_preflight_report(path: Path) -> dict: ...
def parse_dry_run_report(path: Path) -> dict: ...
def parse_live_quality_report(path: Path) -> dict: ...
def parse_after_close_report(path: Path) -> dict: ...
```

Each wrapper delegates to `parse_key_value_md` and adds `report_type`.

- [ ] **Step 3: Implement CSV parsers**

Create:

```python
def parse_watchlist_csv(path: Path): ...
def parse_signals_csv(path: Path): ...
```

Prefer pandas if available; otherwise return a minimal table object with `.empty`, `.columns`, and `.to_dict("records")` support so tests and dashboard remain usable without pandas.

- [ ] **Step 4: Run GREEN for parser tests**

Run:

```text
python -m pytest overnight_quant/tests/test_phase42_dashboard.py -q
```

Parser tests should pass; command/dashboard tests may still fail until Task 3.

## Task 3: Implement Safe Dashboard Command Layer

**Files:**
- Create: `overnight_quant/ui/dashboard.py`

- [ ] **Step 1: Define command whitelist**

Add:

```python
APPROVED_ACTIONS = {
    "preflight": [...],
    "live_dry_run": [...],
    "after_close_live": [...],
    "morning_replay_live": [...],
    "sell_plan_live": [...],
    "demo_after_close": [...],
    "demo_scan": [...],
}
```

Every command must be a list of arguments beginning with `sys.executable`; none can be user-composed shell strings. The only scan commands are `--mode live --dry-run` and demo scan.

- [ ] **Step 2: Implement command runner**

Add:

```python
def run_approved_action(action: str, timeout: int = 180) -> dict:
    if action not in APPROVED_ACTIONS:
        return {"ok": False, "error": "ACTION_NOT_APPROVED"}
    completed = subprocess.run(APPROVED_ACTIONS[action], shell=False, timeout=timeout, capture_output=True, text=True)
    return {"ok": completed.returncode == 0, "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
```

- [ ] **Step 3: Implement report loading and summary helpers**

Add:

```python
def load_dashboard_state(mode: str = "live", root: Path | None = None) -> dict: ...
def build_status_conclusion(state: dict) -> str: ...
```

These helpers read latest preflight, dry-run, quality, after-close, morning replay, signals, watchlist, sell-plan, lifecycle, and review artifacts. They never write orders or tickets.

- [ ] **Step 4: Implement Streamlit rendering with import guard**

`dashboard.py` may import Streamlit inside `main()` only. If unavailable, it should raise/print `UI_DEPENDENCY_MISSING` when run directly. Render six tabs:

```text
Today Overview
Preflight / Intraday Check
Tail Dry-run
After-Close Watchlist
Morning Replay
Positions / Sell / Review
```

Buttons call only `run_approved_action()`.

- [ ] **Step 5: Run dashboard safety tests**

Run:

```text
python -m pytest overnight_quant/tests/test_phase42_dashboard.py -q
```

Expected: all Phase 4.2 tests pass.

## Task 4: Implement Launcher And Documentation

**Files:**
- Create: `requirements-ui.txt`
- Create: `overnight_quant/scripts/run_dashboard.py`
- Modify: `overnight_quant/README.md`
- Modify: `overnight_quant/RELEASE_NOTES.md`

- [ ] **Step 1: Add optional requirements**

`requirements-ui.txt`:

```text
streamlit>=1.30
pandas>=2.0
```

- [ ] **Step 2: Add launcher**

`run_dashboard.py` must check whether Streamlit is importable. If not, print:

```text
UI_DEPENDENCY_MISSING: please run pip install -r requirements-ui.txt
```

and return exit code `2`. If installed, execute:

```python
subprocess.run([sys.executable, "-m", "streamlit", "run", str(dashboard_path)], shell=False)
```

- [ ] **Step 3: Document usage**

README adds:

```text
pip install -r requirements-ui.txt
python overnight_quant/scripts/run_dashboard.py
```

and states the dashboard only runs whitelisted dry-run/analysis commands, with no formal live ticket button and no automatic execution.

Release notes add Phase 4.2.

- [ ] **Step 4: Run full verification**

Run:

```text
python -m pytest overnight_quant/tests -q
python overnight_quant/scripts/run_dashboard.py
```

If Streamlit is not installed, the second command should print `UI_DEPENDENCY_MISSING` and exit `2`; if installed, do not leave a long-running server running in the final state.

## Task 5: Commit Boundaries

- [ ] **Step 1: Verify git boundary**

Run:

```text
git status --short --untracked-files=all
git diff --stat
```

Only Phase 4.2 source/test/doc files should be tracked changes. Generated records/reports/examples/cache/backtest outputs and task-external files must remain unstaged.

- [ ] **Step 2: Stage exact files**

Run:

```text
git add -- \
  requirements-ui.txt \
  overnight_quant/ui/__init__.py \
  overnight_quant/ui/dashboard.py \
  overnight_quant/ui/result_parser.py \
  overnight_quant/scripts/run_dashboard.py \
  overnight_quant/tests/test_phase42_dashboard.py \
  overnight_quant/README.md \
  overnight_quant/RELEASE_NOTES.md
```

- [ ] **Step 3: Commit implementation**

Run:

```text
git commit -m "Add local dashboard for live observation"
```

Do not stage `AGENTS.md`, the Chinese manual, generated reports/records, cache, or backtest outputs.
