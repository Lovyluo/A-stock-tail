from __future__ import annotations

import argparse
import sys
from datetime import datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.astock_client import AStockClient
from overnight_quant.data.demo_data import demo_quotes
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.execution.state_manager import config_for_mode
from overnight_quant.reports.auction_report import write_auction_csv, write_auction_report
from overnight_quant.strategy.auction_observation import AuctionObservationAnalyzer, load_auction_config, load_trading_day_candidates


def run_auction_observation(mode: str = "demo", trade_date: str | None = None, config: dict | None = None, client=None, now: datetime | None = None, candidate_rows: list[dict] | None = None) -> dict:
    runtime = config_for_mode(config or load_auction_config(), mode)
    current = _demo_now() if mode == "demo" and now is None else _coerce_now(now)
    paths = runtime.get("paths", {})
    rows = candidate_rows if candidate_rows is not None else load_trading_day_candidates(paths["records_dir"])
    if mode == "demo" and not rows:
        rows = [{"code": item["code"], "name": item["name"], "source_bucket": "watchlist"} for item in demo_quotes()[:3]]
    result = AuctionObservationAnalyzer(client or AStockClient(mode, now=current), runtime, mode, current, rows).analyze(trade_date)
    result["report_path"] = write_auction_report(result, paths["reports_dir"])
    result["csv_path"] = write_auction_csv(result, paths["records_dir"])
    return result


def _coerce_now(value: datetime | None) -> datetime:
    current = value or datetime.now(CN_TZ)
    return (current.replace(tzinfo=CN_TZ) if current.tzinfo is None else current).astimezone(CN_TZ)


def _demo_now() -> datetime:
    current = datetime.now(CN_TZ)
    return datetime.combine(current.date(), time(9, 27), tzinfo=CN_TZ)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate call-auction observation context.")
    parser.add_argument("--mode", choices=["demo", "live"], default="demo")
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    result = run_auction_observation(args.mode, args.date)
    print(f"Status: {result['status']}")
    print(f"Market Auction Bias: {result['market_auction_bias']}")
    print(f"Report: {result['report_path']}")
    print(f"CSV: {result['csv_path']}")
    print("Risk Notice: observation only; not investment advice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
