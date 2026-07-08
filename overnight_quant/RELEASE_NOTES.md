# Release Notes

## Phase 4.2.6 - Guarded Formal Live Dashboard Action

- Adds a guarded dashboard action for formal live tail scan while preserving the fixed whitelist and `shell=False` command execution.
- Blocks the formal live action outside the 14:25-14:55 tail window with a clear Chinese/English warning instead of running the command.
- Replaces raw terminal output in the dashboard with concise action feedback and refreshed parsed result sections.
- Parses the latest formal manual ticket into direct buy-review rows: stock code, price range, position amount, quantity, stop loss, and next-morning sell plan.
- Keeps manual execution boundaries: no order recording, no automated orders, no broker clicks, and no broker trading APIs.

## Phase 4.2.5 - Signal And Watchlist Output Semantics

- Writes `signals.csv` with only risk-approved tail candidates; rejected rows move to `signal_rejections.csv` for audit.
- Writes runtime CSV files with UTF-8 BOM so Chinese stock names render correctly in Windows spreadsheet tools.
- Keeps C-class "do not chase" rows in after-close markdown reports while excluding them from `next_morning_watchlist_*.csv` formal observation files.
- Preserves strategy, scoring, risk gates, dashboard whitelist, and manual-only safety boundaries.

## Phase 4.2.4 - Tail Candidate Display Guard

- Separates Tail tab observable candidates from rejected audit rows so limit-up, ST, overextended, and other rejected rows are not shown as observation candidates.
- Preserves strategy, parser, whitelist, and manual-only safety boundaries; this is a dashboard display fix.

## Phase 4.2.3 - Premium Dashboard UI Upgrade

- Reworks the local dashboard into a dark financial-terminal interface with a top status bar, hero conclusion, glass-style cards, neon lime accents, and red/yellow/green risk badges.
- Splits the result view into Overview, Preflight, Live Dry-run, Tail, After Close, Morning Replay, and Sell Plan tabs while keeping parsed Chinese results visible directly in the page.
- Preserves the existing safe command whitelist, `shell=False`, result parser boundary, no formal live scan, no ticket generation, no manual order recording, and no trading execution.

## Phase 4.2.2 - Dashboard Inline Result Rendering

- Shows parsed preflight, dry-run, after-close, morning replay, sell-plan, lifecycle, and review results directly inside the dashboard.
- Moves `reports/` and `records/` paths into audit expanders so normal use no longer requires opening generated files manually.
- Keeps generated markdown/CSV artifacts as reviewable audit records and preserves the existing safe-action whitelist with no trading execution path.

## Phase 4.2.1 - Dashboard I18n And Live Reference Polish

- Makes the local dashboard default to Chinese and live-reference mode, with one-click `中文 / English` interface switching.
- Reworks the dashboard into clearer status cards, concise action buttons, risk copy, and table-first sections instead of mostly raw JSON.
- Labels demo actions as demo/test only and marks demo fallback as not valid for live reference observation.
- Preserves the fixed safe-action whitelist and still does not expose formal live ticket generation, manual trade recording, automated orders, broker clicks, or broker APIs.

## Phase 4.2 - Local Dashboard For Live Observation

- Adds an optional Streamlit launcher and dashboard for local viewing of preflight, dry-run, after-close, morning replay, sell-plan, lifecycle, and review outputs.
- Adds lightweight parsers for markdown key-value reports and CSV watchlists/signals with graceful missing-file handling.
- Restricts dashboard command execution to a fixed whitelist using `subprocess.run(..., shell=False)`; no arbitrary shell commands are accepted.
- Does not expose formal live ticket generation, manual trade recording, automated orders, broker clicks, or broker trading APIs.

## Phase 4.1.1 - Morning Previous-Close Replay Watchlist

- Adds an explicit live-only `--replay-previous-close` catch-up mode for pre-market and call-auction observation planning when the prior after-close report was missed.
- Requires replay-sensitive quote, theme, K-line, and quality metadata to match the previous likely trading day before formal rows can be emitted.
- Writes isolated `morning_replay_*` report and CSV artifacts with replay lineage, `previous_close_replay` fidelity, and reconstruction warnings.
- Does not generate manual tickets, record or submit orders, click broker software, or integrate with broker APIs.

## Phase 4.1 - After-Close Next-Morning Watchlist

- Adds a read-only after-close analysis command that writes a next-morning observation report and CSV watchlist.
- Adds conservative non-trading-day, pre-close, live-fallback, and freshness/safety-quality gates before any formal live observation rows are emitted.
- Keeps demo output isolated under `examples/` and live output under real research state directories.
- Does not generate manual tickets, record or submit orders, click broker software, or integrate with broker APIs.

## Phase 3.2b-2f - Expanded Bounded Real Preparation Scope

- Adds explicit `--real-request-scope minimal|expanded`, retaining `minimal` as the one-code/ten-day default while allowing an enabled maximum of three codes/thirty-one days.
- Adds per-code daily-bars/metadata outcome disclosure plus `partial_success` and `failed_codes` reporting for bounded preparation audits.
- Preserves exact-range raw-cache replay, permitted endpoint boundaries, conservative unknown-safety rejection, and offline-only `daily_proxy` consumption.
- Keeps outputs labeled `DAILY_PROXY` only and does not add current/live backfill or automatic execution.

## Phase 3.2b-2e - Real Cache Replay Validation

- Adds explicit raw-cache replay counters, including cache enabled state and cache-read failure totals, to preparation audit output.
- Verifies repeated real-first-run input can replay cached dated bars and metadata with zero repeat network requests.
- Verifies processed research inputs remain stable across cache replay and remain offline `DAILY_PROXY` inputs.
- Keeps real request scope, permitted endpoints, conservative safety handling, and no-automation boundaries unchanged.

## Phase 3.2b-2d - Minimal Real Historical Preparation Client

- Adds an explicitly enabled real preparation path limited to one code and ten natural days on top of the existing outer request gates.
- Fetches only Baidu dated daily K-line rows and Eastmoney stable `name` / `list_date` metadata, with ignored raw-response caching and source-error audit output.
- Leaves benchmark, theme, capital, price-limit, ST, and suspension confirmation unavailable unless historically supplied, so conservative safety rejection remains in force.
- Keeps backtests offline and labels resulting prepared data as `DAILY_PROXY` only, not `strict_historical` validation or evidence of profitability.

## Phase 3.2b-2c - Fake-Real Historical Request Gate

- Adds `--enable-real-astock-request` validation, a 3-code hard limit, and a 31-day date-range limit.
- Adds injected fake-real client tests for dated history, cache/audit behavior, safety-field rejection, and offline `daily_proxy` consumption.
- Leaves real HTTP/TCP historical clients intentionally unimplemented; enabled non-dry CLI runs return `REAL_CLIENT_NOT_IMPLEMENTED_IN_PHASE_3_2C`.
- Preserves `DAILY_PROXY`-only disclosure and does not add live backfills, automated execution, or `strict_historical` validation.

## Phase 3.2b-2b - Mocked Historical Adapter Skeleton

- Adds an injected mock-client contract for bounded `a-stock-data` historical preparation tests only.
- Enforces the 10-code hard limit, minimum sleep validation, cache/audit contract, and explicit `REAL_NETWORK_DISABLED_IN_PHASE_3_2B_2B` CLI guard.
- Records uppercase truth levels and conservatively leaves unsupported safety, theme, capital, and tail fields unknown or unavailable.
- Does not implement real network requests, real historical collection, automated execution, or `strict_historical` validation.

## Phase 3.2b-1.5 - Positive Prepared Fixture Validation

- Adds explicit `--sample-profile neutral|positive` selection for offline sample preparation.
- Keeps neutral sample behavior unchanged while a deterministic positive fixture validates one prepared `daily_proxy` trade and fee/report output.
- Labels positive fixture theme and capital values as `sample_fixture`; they are not live-filled or strict historical evidence.
- Leaves intraday tail pullback unavailable and does not change the engine, scoring, risk gates, fees, or BUY threshold.

## Phase 3.2b-1 - Offline Historical Data Preparation

- Adds deterministic `sample` and local CSV-only `local-raw` preparation for `daily_proxy` research.
- Produces processed input files plus manifest, field-coverage, source-error, and preparation reports without touching trade state.
- Keeps unconfirmed safety fields empty and discloses unavailable historical theme, capital, and intraday-tail data.
- Does not add network collection or claim `strict_historical` validation.

## Phase 3.2a - Local Daily Proxy Backtest

- Adds offline local CSV input and a clearly labeled `daily_proxy` fidelity route.
- Keeps uncertain safety fields as BUY rejections and reports unavailable theme, capital, and tail fields.
- Adds market-direction proxy disclosure, field coverage, and rejection output files.
- Does not treat daily proxy results as full historical validation of the strategy.

## Phase 3.1 - Sample Exact Backtest

- Adds an offline deterministic historical sample provider, backtest event engine, metrics, and summary/data-quality reports.
- Exercises take-profit, stop-loss, limit-up rejection, conservative intraday conflict handling, and limit-down exit carry behavior.
- Writes generated results only to ignored `backtest_outputs/` directories and does not touch real trading state.
- Limits its claim deliberately: `sample_exact` validates engine behavior and event order only, not strategy profitability.

## v0.2.6 - State Isolation And Release Hardening

- Separates committed demonstration outputs under `examples/` from clean real-run state under `records/` and `reports/`.
- Routes demo scan and demo sell-plan commands to example state automatically.
- Keeps live scans, preflight output, and default manual trade recording in real state only.
- Adds `run_reset_state.py` for safe real-state output cleanup with dry-run and confirmation support.

## Version History

- `v0.1.0`: MVP demo scan, filters, scoring, risk gates, and manual BUY ticket.
- `v0.2.0`: Live adapter attempt/fallback and live data-quality reporting.
- `v0.2.1`: Trading-session and data-freshness gates.
- `v0.2.2`: Live field-coverage and safety-field reporting improvements.
- `v0.2.3`: Preflight, dry-run, and live scan summary workflow.
- `v0.2.4`: Manual BUY recording and sell lifecycle tracking.
- `v0.2.5`: Manual SELL recording, realized PnL, fees, and single-trade review.
- `v0.2.6`: Real/example state isolation and release hardening.
- `v0.3.2b-1`: Offline deterministic data preparation for daily-proxy research.
- `v0.3.2b-1.5`: Positive prepared-fixture trade lifecycle validation.
- `v0.3.2b-2b`: Mock-only bounded historical adapter and audit skeleton.
