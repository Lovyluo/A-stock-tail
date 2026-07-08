# Roadmap

This roadmap keeps A-stock-tail focused on manual research and observation. Automated trading, brokerage API integration, and brokerage UI control are out of scope.

## v0.1 - Initial Release

- Package the A-share data Skill with attribution to upstream `a-stock-data`.
- Publish the `overnight_quant` example app for local observation workflows.
- Provide dashboard startup scripts and deployment notes.
- Keep runtime records, reports, logs, caches, and upstream clone directories out of Git.
- Add tests for strategy filters, backtest behavior, dashboard parsing, and operation safety.

## v0.1.1 - Release Hardening

- Verify a clean GitHub clone can install dependencies and run tests.
- Add GitHub Actions CI on Windows with Python 3.12.
- Add risk disclaimer and issue templates.
- Clarify README safety boundaries and test commands.
- Preserve upstream attribution in NOTICE.

## v0.2 - Data Quality And UX

- Improve source health reporting for Eastmoney, Tencent, Sina, Baidu, Tonghuashun, and mootdx routes.
- Make dashboard status messages clearer when data sources disconnect.
- Add more deterministic demo fixtures for common workflows.
- Improve documentation for local cache, backtest data preparation, and data fidelity modes.

## v0.3 - Research Workflow Hardening

- Add richer portfolio review summaries for manual decision support.
- Improve intraday observation refresh ergonomics without adding trading automation.
- Add stricter audit trails for generated reports and manual tickets.
- Expand CI checks for packaging, import smoke tests, and documentation links.

