# Phase 4.2.1 Dashboard I18n And Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the optional local dashboard into a polished live-observation console with Chinese/English one-click switching and a clearer formal-reference workflow.

**Architecture:** Keep the dashboard as a UI-only layer. Add translation dictionaries, status presentation helpers, card/table rendering helpers, and a stronger visual hierarchy inside `overnight_quant/ui/dashboard.py`; keep command execution whitelisted and unchanged in scope.

**Tech Stack:** Streamlit, pandas where available, existing report parsers, Python standard library, pytest, and in-app browser inspection of the local Streamlit page.

---

## Scope Lock

Modify:

```text
overnight_quant/ui/dashboard.py
overnight_quant/tests/test_phase42_dashboard.py
overnight_quant/README.md
overnight_quant/RELEASE_NOTES.md
```

Do not modify strategy, risk, scan, sell, order recording, ticket generation, or backtest modules. Do not add broker APIs, automatic orders, automatic clicking, arbitrary shell commands, formal live ticket buttons, or direct manual-order writing.

## Task 1: Add I18n And Formal-Reference Tests

**Files:**
- Modify: `overnight_quant/tests/test_phase42_dashboard.py`

- [ ] **Step 1: Write failing language tests**

Add tests:

```python
def test_dashboard_defaults_to_chinese_live_reference_mode():
    from overnight_quant.ui.dashboard import DEFAULT_LANGUAGE, DEFAULT_MODE, t
    assert DEFAULT_LANGUAGE == "zh"
    assert DEFAULT_MODE == "live"
    assert t("zh", "app_title") == "A股隔夜量化观察台"
    assert t("en", "app_title") == "A-Share Overnight Quant Dashboard"

def test_action_labels_are_localized_and_demo_is_marked_as_demo():
    from overnight_quant.ui.dashboard import action_label
    assert action_label("zh", "live_dry_run") == "Live Dry-run"
    assert "演示" in action_label("zh", "demo_scan")
    assert "Demo" in action_label("en", "demo_scan")
```

- [ ] **Step 2: Write failing safety and presentation tests**

Add tests:

```python
def test_dashboard_safety_notice_is_bilingual():
    from overnight_quant.ui.dashboard import safety_notice
    assert "不会自动下单" in safety_notice("zh")
    assert "does not place orders" in safety_notice("en")

def test_status_badge_color_contract():
    from overnight_quant.ui.dashboard import status_badge
    assert status_badge("WATCHLIST_READY")["tone"] == "green"
    assert status_badge("DATA_FALLBACK_DEMO")["tone"] == "red"
    assert status_badge("MISSING")["tone"] == "gray"
```

- [ ] **Step 3: Run RED**

Run:

```text
python -m pytest overnight_quant/tests/test_phase42_dashboard.py -q
```

Expected: import or attribute failures because the i18n/presentation helpers do not exist yet.

## Task 2: Implement I18n, Chinese Default, And Safer Copy

**Files:**
- Modify: `overnight_quant/ui/dashboard.py`

- [ ] **Step 1: Add language constants and translation helpers**

Add:

```python
DEFAULT_LANGUAGE = "zh"
DEFAULT_MODE = "live"
LANGUAGES = {"zh": "中文", "en": "English"}
TEXT = {"zh": {...}, "en": {...}}
def t(language: str, key: str) -> str: ...
def action_label(language: str, action: str) -> str: ...
def safety_notice(language: str) -> str: ...
```

Use Chinese as the default UI language and keep English available from one radio/select control.

- [ ] **Step 2: Rewrite dashboard text**

Replace mixed English/raw labels with localized text:

```text
中文: A股隔夜量化观察台, 今日总览, 盘前检查, 尾盘观察, 盘后观察池, 早盘 Replay, 持仓/卖出/复盘
English: A-Share Overnight Quant Dashboard, Overview, Preflight, Tail Dry-run, After-Close Watchlist, Morning Replay, Positions / Sell / Review
```

Keep demo actions visually labeled as demo/test only; default mode remains live.

- [ ] **Step 3: Run focused tests**

Run:

```text
python -m pytest overnight_quant/tests/test_phase42_dashboard.py -q
```

Expected: language/safety tests pass.

## Task 3: Improve Visual Hierarchy And Reference Semantics

**Files:**
- Modify: `overnight_quant/ui/dashboard.py`
- Modify: `overnight_quant/tests/test_phase42_dashboard.py`

- [ ] **Step 1: Add failing tests for CSS and reference warnings**

Add tests:

```python
def test_dashboard_css_contains_card_and_primary_button_styles():
    from overnight_quant.ui.dashboard import DASHBOARD_CSS
    assert ".oq-card" in DASHBOARD_CSS
    assert ".oq-action-grid" in DASHBOARD_CSS

def test_build_status_conclusion_blocks_demo_fallback_as_not_reference():
    from overnight_quant.ui.dashboard import build_status_conclusion
    state = {"dry_run": {"candidate_source": "demo_fallback"}, "preflight": {}}
    assert "不能作为实盘参考" in build_status_conclusion(state, language="zh")
```

- [ ] **Step 2: Implement card/status helpers**

Add:

```python
def status_badge(status: str) -> dict[str, str]: ...
def render_status_card(st, title: str, value: str, tone: str, caption: str = "") -> None: ...
def render_table_or_empty(st, table, empty_text: str) -> None: ...
```

Use concise cards and tables instead of mostly `st.json`. Keep details available in expanders.

- [ ] **Step 3: Implement polished tabs**

Each tab should show:

```text
1. obvious action button area
2. status cards
3. table, if available
4. data-quality / risk warning
5. raw detail expander
```

The dashboard should say results are "live reference observation" only when not demo fallback/stale/safety unknown. Do not call it a buy signal.

- [ ] **Step 4: Run focused tests**

Run:

```text
python -m pytest overnight_quant/tests/test_phase42_dashboard.py -q
```

Expected: all dashboard tests pass.

## Task 4: Documentation, Runtime, And Browser Verification

**Files:**
- Modify: `overnight_quant/README.md`
- Modify: `overnight_quant/RELEASE_NOTES.md`

- [ ] **Step 1: Update documentation**

README should say:

```text
Dashboard defaults to live mode and Chinese UI.
Use 中文 / English to switch language.
Demo actions remain available only for testing and are labeled as demo.
The dashboard provides reference observation only; manual trading remains outside the app.
```

Release notes add Phase 4.2.1.

- [ ] **Step 2: Run verification**

Run:

```text
python -m pytest overnight_quant/tests -q
python overnight_quant/scripts/run_dashboard.py
```

If Streamlit is missing, install optional UI dependencies:

```text
python -m pip install -r requirements-ui.txt
```

- [ ] **Step 3: Start and inspect the local page**

Run the dashboard, then inspect `http://localhost:8501` with the available browser tooling. Verify:

```text
Chinese default title is visible
English switch is visible
buttons are concise and visible
demo is clearly marked
no formal ticket/order button is visible
layout is not mostly raw JSON
```

Stop any background Streamlit process after inspection.

## Task 5: Commit Boundaries

- [ ] **Step 1: Confirm clean staging boundary**

Run:

```text
git status --short --untracked-files=all
git diff --stat
```

Only Phase 4.2.1 source/test/doc files should be modified. Do not stage `AGENTS.md`, the Chinese manual, reports, records, examples, cache, or backtest outputs.

- [ ] **Step 2: Stage exact files**

Run:

```text
git add -- \
  overnight_quant/ui/dashboard.py \
  overnight_quant/tests/test_phase42_dashboard.py \
  overnight_quant/README.md \
  overnight_quant/RELEASE_NOTES.md
```

- [ ] **Step 3: Commit**

Run:

```text
git commit -m "Polish local dashboard with bilingual live reference UI"
```
