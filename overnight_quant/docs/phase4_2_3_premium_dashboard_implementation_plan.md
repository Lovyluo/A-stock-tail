# Phase 4.2.3 Premium Dashboard UI Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the local Streamlit dashboard into a premium dark financial-terminal UI while preserving the existing read-only, whitelisted, manual-trading-only safety boundary.

**Architecture:** Keep `overnight_quant/ui/dashboard.py` as a UI-only layer over the existing parsed state. Do not change `result_parser.py`, strategy logic, risk logic, command whitelist semantics, shell execution policy, or any trading/order modules. Add presentation helpers, premium CSS, top status bar, hero conclusion, risk/status badges, and better table rendering inside the dashboard.

**Tech Stack:** Python, Streamlit, existing markdown/CSV parsers, pytest, in-app browser visual smoke check.

---

## Files

- Modify: `overnight_quant/ui/dashboard.py`
  - Replace the light theme with premium dark terminal CSS.
  - Add top status bar, hero conclusion, risk badges, watchlist table styling hooks, and tab labels.
  - Keep `APPROVED_ACTIONS` unchanged and keep `subprocess.run(..., shell=False)`.
- Modify: `overnight_quant/tests/test_phase42_dashboard.py`
  - Add tests for dark theme CSS, premium layout copy, tab labels, risk badge rendering, and no trading execution exposure.
- Modify: `overnight_quant/README.md`
  - Document the premium dashboard style and remind users that results are inline while files remain audit artifacts.
- Modify: `overnight_quant/RELEASE_NOTES.md`
  - Add Phase 4.2.3 summary.

## Non-Goals

- Do not add formal live scan.
- Do not generate manual tickets.
- Do not record manual orders.
- Do not call broker APIs.
- Do not click broker software.
- Do not change `result_parser.py`.
- Do not change strategy, risk, sell, record, review, or backtest behavior.

## Task 1: Write Premium UI Contract Tests

- [ ] **Step 1: Add failing tests**

Add tests to `overnight_quant/tests/test_phase42_dashboard.py` for:

```python
def test_premium_dashboard_css_has_dark_financial_terminal_theme():
    from overnight_quant.ui.dashboard import DASHBOARD_CSS

    assert "--oq-bg" in DASHBOARD_CSS
    assert "#07111f" in DASHBOARD_CSS
    assert "--oq-lime" in DASHBOARD_CSS
    assert "backdrop-filter" in DASHBOARD_CSS
    assert ":hover" in DASHBOARD_CSS
```

```python
def test_premium_tabs_include_tail_and_sell_plan():
    from overnight_quant.ui.dashboard import premium_tab_labels

    assert premium_tab_labels("en") == [
        "Overview",
        "Preflight",
        "Live Dry-run",
        "Tail",
        "After Close",
        "Morning Replay",
        "Sell Plan",
    ]
```

```python
def test_risk_badge_html_uses_status_tone_classes():
    from overnight_quant.ui.dashboard import render_badge_html

    assert "oq-badge-green" in render_badge_html("PASS")
    assert "oq-badge-red" in render_badge_html("DATA_FALLBACK_DEMO")
    assert "oq-badge-yellow" in render_badge_html("NO_TRADE")
```

```python
def test_hero_conclusion_contains_direct_result_copy():
    from overnight_quant.ui.dashboard import hero_conclusion

    state = {
        "conclusion": "Current output is dry-run only.",
        "dry_run": {"candidate_source": "live", "valid_for_trading_observation": "NO"},
        "reference_summary": {"reason": "dry_run_only", "tone": "yellow"},
    }

    result = hero_conclusion(state, "en")

    assert "Current output is dry-run only." in result["headline"]
    assert result["candidate_source"] == "live"
    assert result["validity"] == "NO"
```

Run:

```bash
python -m pytest overnight_quant/tests/test_phase42_dashboard.py -q
```

Expected: fail because premium helpers and CSS tokens are not present yet.

## Task 2: Implement Premium Presentation Helpers

- [ ] **Step 1: Add helper functions**

In `overnight_quant/ui/dashboard.py`, add:

```python
def premium_tab_labels(language: str) -> list[str]: ...
def render_badge_html(value: str) -> str: ...
def hero_conclusion(state: dict[str, Any], language: str) -> dict[str, str]: ...
```

Keep them pure and testable.

- [ ] **Step 2: Run tests**

```bash
python -m pytest overnight_quant/tests/test_phase42_dashboard.py -q
```

Expected: dashboard tests pass.

## Task 3: Apply Dark Financial Terminal Layout

- [ ] **Step 1: Replace CSS**

Update `DASHBOARD_CSS` with:

- dark navy background;
- glassmorphism cards;
- neon lime accent;
- red/yellow/green badges;
- button hover effects;
- tab and table polish.

- [ ] **Step 2: Render top status bar and hero**

In `main()`, after loading state:

- render a top status bar with mode, session, candidate source, and validity;
- render hero conclusion card;
- keep safety notice.

- [ ] **Step 3: Rework tabs**

Use:

```text
Overview / Preflight / Live Dry-run / Tail / After Close / Morning Replay / Sell Plan
```

Map Tail to the same signals table already used by tail dry-run. Do not create new scan behavior.

- [ ] **Step 4: Run tests**

```bash
python -m pytest overnight_quant/tests/test_phase42_dashboard.py -q
```

Expected: pass.

## Task 4: Docs, Browser Verification, Commit

- [ ] **Step 1: Update docs**

Update README and release notes with Phase 4.2.3.

- [ ] **Step 2: Run full tests**

```bash
python -m pytest overnight_quant/tests -q
```

Expected: all tests pass.

- [ ] **Step 3: Browser smoke test**

Start Streamlit in the background, open `http://localhost:8501`, and verify:

- dark terminal UI is visible;
- top status bar is visible;
- hero conclusion is visible;
- inline Chinese results are visible;
- audit files remain behind expanders;
- no manual ticket/order/trading controls appear.

- [ ] **Step 4: Commit**

Stage exactly:

```bash
git add -- \
  overnight_quant/ui/dashboard.py \
  overnight_quant/tests/test_phase42_dashboard.py \
  overnight_quant/README.md \
  overnight_quant/RELEASE_NOTES.md
```

Commit:

```bash
git commit -m "Upgrade dashboard with premium dark terminal UI"
```

Do not stage `AGENTS.md`, `overnight_quant_实盘使用手册.md`, reports, records, examples, cache, or backtest outputs.
