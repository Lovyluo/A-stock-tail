from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.astock_client import AStockClient, DATA_CONTEXT_PREVIOUS_CLOSE_REPLAY
from overnight_quant.data.market_calendar import CN_TZ, effective_tail_observation_trade_day, previous_likely_cn_trade_day
from overnight_quant.execution.state_manager import config_for_mode
from overnight_quant.reports.after_close_report import write_after_close_report, write_watchlist_csv
from overnight_quant.strategy.after_close_analysis import AfterCloseAnalyzer, load_after_close_config


def run_after_close_analysis(
    mode: str = "demo",
    trade_date: str | None = None,
    config: dict | None = None,
    client=None,
    now: datetime | None = None,
    replay_previous_close: bool = False,
) -> dict:
    runtime_config = config_for_mode(config or load_after_close_config(), mode)
    analysis_mode = "previous_close_replay" if replay_previous_close else "after_close"
    if replay_previous_close and mode != "live":
        raise ValueError("REPLAY_REQUIRES_LIVE_MODE")
    analyzer_now = _normalize_now(now)
    effective_trade_date = trade_date
    tail_start = runtime_config.get("tail_observation", {}).get("live_start", "14:50")
    carryover_trade_day = effective_tail_observation_trade_day(analyzer_now, tail_start) if mode == "live" and not replay_previous_close else None
    if carryover_trade_day and carryover_trade_day != analyzer_now.date() and effective_trade_date is None:
        effective_trade_date = carryover_trade_day.isoformat()
    if client is not None:
        runtime_client = client
    elif replay_previous_close:
        runtime_client = AStockClient(
            mode,
            now=analyzer_now,
            data_context=DATA_CONTEXT_PREVIOUS_CLOSE_REPLAY,
            expected_data_date=previous_likely_cn_trade_day(analyzer_now).isoformat(),
        )
    elif carryover_trade_day and effective_trade_date == carryover_trade_day.isoformat():
        runtime_client = AStockClient(
            mode,
            now=analyzer_now,
            data_context=DATA_CONTEXT_PREVIOUS_CLOSE_REPLAY,
            expected_data_date=effective_trade_date,
        )
    else:
        runtime_client = AStockClient(mode, now=analyzer_now)
    result = AfterCloseAnalyzer(runtime_client, runtime_config, mode, analyzer_now, analysis_mode=analysis_mode).analyze(effective_trade_date)
    if carryover_trade_day and effective_trade_date == carryover_trade_day.isoformat() and not replay_previous_close:
        result["after_close_carryover"] = "YES"
        result["observation_date"] = analyzer_now.date().isoformat()
        result["freshness_basis"] = "previous_close_expected"
    paths = runtime_config.get("paths", {})
    result["report_path"] = write_after_close_report(result, paths["reports_dir"])
    result["watchlist_csv"] = write_watchlist_csv(result, paths["records_dir"])
    return result


def _normalize_now(now: datetime | None) -> datetime:
    current = now or datetime.now(CN_TZ)
    if current.tzinfo is None:
        return current.replace(tzinfo=CN_TZ)
    return current.astimezone(CN_TZ)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a tail-observation watchlist after 14:50 or replay it after close.")
    parser.add_argument("--mode", choices=["demo", "live"], default="demo")
    parser.add_argument("--date", default=None)
    parser.add_argument("--replay-previous-close", action="store_true")
    args = parser.parse_args()
    if args.replay_previous_close and args.mode != "live":
        print("REPLAY_REQUIRES_LIVE_MODE")
        return 2
    if args.replay_previous_close and args.date:
        print("REPLAY_DATE_OVERRIDE_UNSUPPORTED")
        return 2
    result = run_after_close_analysis(
        mode=args.mode,
        trade_date=args.date,
        replay_previous_close=args.replay_previous_close,
    )
    categories = result["categories"]
    print(f"Mode: {result['mode']}")
    print(f"Analysis Mode: {result.get('analysis_mode', 'after_close')}")
    print(f"Analysis Context: {result.get('analysis_context', '')}")
    print(f"Status: {result['status']}")
    print(f"Session State: {result['session_state']}")
    print(f"Candidate Source: {result['candidate_source']}")
    print(f"Valid For Trading Observation: {result['valid_for_trading_observation']}")
    if result.get("after_close_carryover") == "YES":
        print(f"After Close Carryover: {result.get('after_close_carryover', '')}")
        print(f"Observation Date: {result.get('observation_date', '')}")
        print(f"Freshness Basis: {result.get('freshness_basis', '')}")
    if result.get("analysis_mode") == "previous_close_replay":
        print(f"Observation Date: {result.get('observation_date', '')}")
        print(f"Replay As Of Date: {result.get('replay_as_of_date', '')}")
        print(f"Freshness Basis: {result.get('freshness_basis', '')}")
    print(f"A Count: {len(categories['A'])}")
    print(f"B Count: {len(categories['B'])}")
    print(f"C Count: {len(categories['C'])}")
    print(f"Report: {result['report_path']}")
    print(f"Watchlist CSV: {result['watchlist_csv']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
