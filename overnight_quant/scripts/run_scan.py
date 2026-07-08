from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.astock_client import AStockClient
from overnight_quant.execution.state_manager import config_for_mode
from overnight_quant.reports.scan_reports import ticket_absence_reasons
from overnight_quant.strategy.yang_yongxing_overnight import YangYongxingOvernightStrategy, load_config


def run_scan(
    mode: str = "demo",
    trade_date: str | None = None,
    allow_outside_session: bool = False,
    dry_run: bool = False,
    config: dict | None = None,
    client=None,
) -> dict:
    runtime_config = config_for_mode(config or load_config(), mode)
    runtime_client = client or AStockClient(mode, allow_outside_session=allow_outside_session)
    return YangYongxingOvernightStrategy(runtime_client, runtime_config).scan(trade_date, dry_run=dry_run)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Yang Yongxing overnight MVP scan.")
    parser.add_argument("--mode", choices=["demo", "live"], default="demo")
    parser.add_argument("--date", default=None)
    parser.add_argument(
        "--allow-outside-session",
        action="store_true",
        help="Allow live BUY ticket generation outside the 14:25-14:55 tail window.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Run live scan rehearsal without generating manual tickets.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    result = run_scan(
        mode=args.mode,
        trade_date=args.date,
        allow_outside_session=args.allow_outside_session,
        dry_run=args.dry_run,
    )

    print("[Overnight Quant Signal]")
    print(f"Mode: {args.mode}")
    print(f"Dry Run: {'YES' if args.dry_run else 'NO'}")
    print(f"Market Gate: {'PASS' if result['market_gate']['pass'] else 'FAIL'}")
    market_gate = result["market_gate"]
    market_reasons = market_gate["reasons"] if market_gate["pass"] else market_gate["reject_reasons"]
    print(f"Market Reasons: {', '.join(market_reasons)}")
    print(f"Candidate Count: {result['candidate_count']}")
    print(f"Candidate Source: {result['candidate_source']}")
    if args.dry_run:
        print("Final Advice: DRY_RUN_ONLY")
    else:
        print(f"Final Advice: {'BUY' if result['selected'] else 'NO_TRADE'}")
    if result["fallback_messages"]:
        print("Fallback:")
        for message in result["fallback_messages"]:
            print(f"- {message}")
    if result["selected"]:
        for idx, stock in enumerate(result["selected"], start=1):
            print(
                f"{idx}. {stock['code']} {stock['name']} score={stock['total_score']} "
                f"price={stock['price']} change={stock['change_pct']}%"
            )
            print(f"   ticket: {stock['ticket_path']}")
    else:
        reasons = [f"market: {', '.join(market_gate.get('reject_reasons', []))}"] if market_gate.get("reject_reasons") else []
        for stock in result["rejected"][:5]:
            reasons.append(f"{stock.get('code')}: {', '.join(stock.get('filter_reject_reasons') or stock.get('risk_gate', {}).get('reasons', []))}")
        print("No trade reasons:")
        for reason in reasons:
            print(f"- {reason}")
    absence_reasons = ticket_absence_reasons(result, dry_run=args.dry_run)
    if absence_reasons:
        print("Buy Ticket Not Generated:")
        for reason in absence_reasons:
            print(f"- {reason}")
    print(f"Signals CSV: {result['signals_csv']}")
    if result.get("signal_rejections_csv"):
        print(f"Signal Rejections CSV: {result['signal_rejections_csv']}")
    if result.get("quality_report_path"):
        print(f"Live Data Quality Report: {result['quality_report_path']}")
    if result.get("dry_run_report_path"):
        print(f"Dry Run Report: {result['dry_run_report_path']}")
    if result.get("scan_summary_path"):
        print(f"Live Scan Summary: {result['scan_summary_path']}")
    print("Risk Notice: research assistant only; no automated orders; not investment advice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
