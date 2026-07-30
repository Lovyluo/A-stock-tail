# A-stock-tail

[![CI](https://github.com/Lovyluo/A-stock-tail/actions/workflows/ci.yml/badge.svg)](https://github.com/Lovyluo/A-stock-tail/actions/workflows/ci.yml)

Repository: https://github.com/Lovyluo/A-stock-tail

A-stock-tail is a personal A-share research workspace built around two pieces:

1. **Data Skill**: `SKILL.md` provides the A-share data-source capability.
2. **Overnight Quant Dashboard**: `overnight_quant/` is an example application for after-close, pre-market, intraday, and sell-plan observation workflows.

The project is designed for manual research, observation, and review. It has no real-market execution integration and does not operate brokerage software.

Read the risk boundary before using the project: [DISCLAIMER.md](DISCLAIMER.md).

## Project Narrative

This repository packages a data Skill together with an overnight observation example app.

- `SKILL.md`: A-share data capability source, adapted from the upstream `a-stock-data` project.
- `overnight_quant/`: Example app and scripts for watchlists, scans, backtests, dashboard views, position updates, and manual sell-plan reminders.
- `overnight_quant/strategy/chip_volume.py`: Chip and volume confidence proxy indicators for observation reports. These are not real holder-cost data and are not trading advice.
- `overnight_quant/strategy/close_confirmation_v1/`: v0.4 industry-resonance close-confirmation research strategy.
- `docs/`, `DEPLOY.md`, and `overnight_quant_实盘使用手册.md`: Operational notes for local deployment and manual use.
- `a-stock-data/`: Local upstream clone only. It is ignored by Git and is not part of the final `A-stock-tail` GitHub repository.

## Safety Boundary

A-stock-tail only generates observation materials:

- watchlists
- scan reports
- backtest outputs
- position review notes
- manual buy/sell plan reminders

It does not:

- submit real orders
- operate brokerage clients or trading pages
- scrape or control brokerage trading pages
- call brokerage trading APIs
- provide guaranteed investment returns

All outputs are for research and manual review only.

## Quick Start

```powershell
cd D:\A-stock

# Create and activate a virtual environment if needed.
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install deploy/runtime dependencies.
python -m pip install -r requirements-deploy.txt

# Run tests.
D:\A-stock\.venv\Scripts\python.exe -m pytest overnight_quant/tests -q

# Start dashboard.
python overnight_quant/scripts/run_dashboard.py
```

Dashboard default URL:

```text
http://localhost:8501/
```

## Verification

Run the full local test suite before changing or releasing the project:

```powershell
D:\A-stock\.venv\Scripts\python.exe -m pytest overnight_quant/tests -q
```

GitHub Actions runs the same test command on Windows with Python 3.12.

## v0.4 Research Status

The former `yang_yongxing_overnight_v1` entry is preserved as
`legacy_frozen_baseline`. It is no longer a formal signal or ticket entry.

`close_confirmation_v1` is currently **research / shadow simulation only**:

- freezes point-in-time data at 14:50;
- scores market, industry, relative strength, price/volume, sourced catalysts, and chip proxies;
- starts event fills at 14:51 and cancels remaining quantity at 14:56;
- applies partial fills, date-aware price limits, suspension, T+1, slippage, impact, and 100-share lots;
- never substitutes daily close data for missing minute data;
- requires a versioned exchange-calendar or benchmark-index trading-date record for 60-day chip inputs;
- never uses demo fallback fields in live, shadow, paper, or replay results.

The historical and shadow acceptance thresholds have not yet been met because the required
point-in-time dataset and observation period are not yet available. No production claim should
be made until all gates in
[the strategy design](overnight_quant/docs/phase7_0_close_confirmation_strategy_design.md)
pass.

PR #5 provides the research framework plus manual and replay snapshot entry points only.
It does not include a real automated market-data collector, and it is not ready to begin the
formal 60-trading-day shadow acceptance period. The production-grade point-in-time collector
is deferred to a separate v0.4.1 phase.

v0.4.1 now includes real-source provider interfaces and a validation matrix. Eastmoney
`push2` stability, a verified industry-breadth backup, and the exact availability semantics of
the 14:50 minute event remain blockers. These providers therefore do not yet authorize the
formal 60-trading-day shadow acceptance period.

See also:

- [Point-in-time data dictionary](overnight_quant/docs/phase7_0_point_in_time_data_dictionary.md)
- [v0.4.1 real collector design](overnight_quant/docs/phase7_1_real_point_in_time_collectors_design.md)
- [v0.4.1 source validation matrix](overnight_quant/docs/phase7_1_source_validation_matrix.md)
- [v0.4 safety boundary](overnight_quant/docs/phase7_0_safety_boundary.md)

## Common Commands

```powershell
# After-close analysis
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_after_close_analysis.py --mode demo

# Tail scan dry-run
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_scan.py --mode demo --dry-run

# Freeze point-in-time records at 14:50
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_close_snapshot_collector.py --input snapshot_records.json --freeze --trade-date 2026-07-30

# Validate real providers without writing a snapshot
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_real_source_validation.py --codes 000001

# Collect real provider records during the close window; no demo fallback
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_close_snapshot_collector.py --live --codes 000001

# Run the new strategy in shadow mode
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_close_confirmation.py --mode shadow --date 2026-07-30

# Sell plan
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_sell_plan.py --mode live

# Dashboard
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_dashboard.py
```

## Repository Hygiene

The final GitHub repository root is `D:\A-stock`.

Ignored local/runtime paths include:

- `a-stock-data/`
- `.venv/`
- `deploy_artifacts/`
- `dashboard_*.log`
- `lan_share_setup.log`
- `overnight_quant/records/*`
- `overnight_quant/reports/*`
- `overnight_quant/backtest_outputs/*`
- `overnight_quant/data/cache/*`

Placeholder `.gitignore` files in runtime directories are preserved so the directory layout remains visible without committing generated outputs.

## Upstream Attribution

The data Skill content is derived from:

- Project: `a-stock-data`
- URL: https://github.com/simonlin1212/a-stock-data
- Author: Simon 林
- License: Apache License 2.0

See `NOTICE.md` and `LICENSE` for attribution and license details. This repository should not present the upstream data Skill work as fully original.
