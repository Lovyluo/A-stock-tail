# A-stock-tail

[![CI](https://github.com/Lovyluo/A-stock-tail/actions/workflows/ci.yml/badge.svg)](https://github.com/Lovyluo/A-stock-tail/actions/workflows/ci.yml)

Repository: https://github.com/Lovyluo/A-stock-tail

A-stock-tail is a personal A-share research workspace built around two pieces:

1. **Data Skill**: `SKILL.md` provides the A-share data-source capability.
2. **Overnight Quant Dashboard**: `overnight_quant/` is an example application for after-close, pre-market, intraday, and sell-plan observation workflows.

The project is designed for manual research, observation, and review. It does **not** place orders automatically, does **not** click brokerage software, and does **not** call any brokerage trading API.

Read the risk boundary before using the project: [DISCLAIMER.md](DISCLAIMER.md).

## Project Narrative

This repository packages a data Skill together with an overnight observation example app.

- `SKILL.md`: A-share data capability source, adapted from the upstream `a-stock-data` project.
- `overnight_quant/`: Example app and scripts for watchlists, scans, backtests, dashboard views, position updates, and manual sell-plan reminders.
- `overnight_quant/strategy/chip_volume.py`: Chip and volume confidence proxy indicators for observation reports. These are not real holder-cost data and are not trading advice.
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

- auto-submit orders
- operate brokerage clients
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

Expected result after the chip/volume proxy module:

```text
337 passed
```

GitHub Actions runs the same test command on Windows with Python 3.12.

## Common Commands

```powershell
# After-close analysis
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_after_close_analysis.py --mode demo

# Tail scan dry-run
D:\A-stock\.venv\Scripts\python.exe overnight_quant/scripts/run_scan.py --mode demo --dry-run

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
