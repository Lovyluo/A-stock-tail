from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.backtest.point_in_time_provider import PointInTimeDataError, PointInTimeProvider
from overnight_quant.data.point_in_time import enforce_formal_no_demo
from overnight_quant.reports.close_confirmation_report import write_close_confirmation_report
from overnight_quant.strategy.registry import (
    build_strategy,
    ensure_default_strategies_registered,
)
from overnight_quant.strategy.yang_yongxing_overnight import load_config


def run_close_confirmation(
    *,
    mode: str = "shadow",
    snapshot_path: str | Path | None = None,
    trade_date: str | None = None,
    config: dict | None = None,
) -> dict:
    day = trade_date or date.today().isoformat()
    runtime_config = config or load_config()
    reports_dir = runtime_config.get("paths", {}).get("reports_dir", "overnight_quant/reports")
    if not snapshot_path:
        snapshot_path = (
            Path("overnight_quant/data/cache/close_confirmation_snapshots/frozen_1450")
            / f"{day}.json"
        )
    try:
        provider = PointInTimeProvider.from_frozen_file(snapshot_path)
        snapshot = provider.snapshot_at(day, "14:50", require_minute_data=True)
        ensure_default_strategies_registered()
        strategy = build_strategy(
            "close_confirmation_v1",
            runtime_config.get("close_confirmation") or {},
        )
        result = strategy.evaluate_snapshot(
            snapshot,
            mode=mode,
        )
        result.setdefault("execution_ok", True)
        result.setdefault("data_ready", False)
    except (FileNotFoundError, PointInTimeDataError, ValueError) as exc:
        result = enforce_formal_no_demo(
            {
                "strategy_name": "close_confirmation_v1",
                "strategy_phase": "research_shadow",
                "status": "POINT_IN_TIME_DATA_UNAVAILABLE",
                "mode": mode,
                "decision_time": f"{day}T14:50:00+08:00",
                "scored": [],
                "shadow_candidates": [],
                "selected": [],
                "tickets": [],
                "orders": [],
                "formal_signal_enabled": False,
                "ticket_enabled": False,
                "execution_ok": True,
                "data_ready": False,
                "data_error": f"{type(exc).__name__}: {exc}",
            },
            mode,
        )
    result["report_path"] = write_close_confirmation_report(result, reports_dir, day)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the close-confirmation strategy in research shadow mode.")
    parser.add_argument("--mode", choices=["shadow", "paper", "replay"], default="shadow")
    parser.add_argument("--snapshot", default=None)
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    result = run_close_confirmation(
        mode=args.mode,
        snapshot_path=args.snapshot,
        trade_date=args.date,
    )
    print("[Close Confirmation Shadow]")
    print(f"Status: {result['status']}")
    print(f"execution_ok: {str(bool(result.get('execution_ok'))).lower()}")
    print(f"data_ready: {str(bool(result.get('data_ready'))).lower()}")
    print(f"Strategy Phase: {result['strategy_phase']}")
    print(f"Decision Time: {result['decision_time']}")
    print(f"Demo Field Count: {result['demo_field_count']}")
    print(f"Shadow Candidate Count: {len(result.get('shadow_candidates') or [])}")
    print(f"Formal Signal Enabled: {result['formal_signal_enabled']}")
    print(f"Ticket Enabled: {result['ticket_enabled']}")
    print(f"Report: {result['report_path']}")
    return 0 if result.get("execution_ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
